from abc import ABC
import asyncio
from typing import AsyncIterator, Dict, Optional, TYPE_CHECKING, Union
from datetime import datetime, timedelta, timezone

from ya_market import RequestorApi, models as ya_models, exceptions

from .api_call_wrapper import api_call_wrapper
from .exceptions import ResourceNotFound
from .resource import Resource
from .yagna_event_collector import YagnaEventCollector
from .events import ResourceClosed

if TYPE_CHECKING:
    from .golem_node import GolemNode


class MarketApiResource(Resource, ABC):
    @classmethod
    def _get_api(cls, node: "GolemNode") -> RequestorApi:
        return RequestorApi(node._ya_market_api)


class Demand(MarketApiResource):
    _event_collecting_task: Optional[asyncio.Task] = None

    def start_collecting_events(self):
        assert self._event_collecting_task is None
        task = asyncio.get_event_loop().create_task(self._process_yagna_events())
        self._event_collecting_task = task

    @api_call_wrapper()
    async def _get_data(self) -> ya_models.Demand:
        #   NOTE: this method is required because there is no get_demand(id)
        #         in ya_market (as there is no matching endpoint in yagna)
        all_demands = await self.api.get_demands()
        try:
            return next(d for d in all_demands if d.demand_id == self.id)
        except StopIteration:
            raise ResourceNotFound('Demand', self.id)

    @classmethod
    async def create_from_properties_constraints(
        cls,
        node: "GolemNode",
        properties: Dict[str, str],
        constraints: str,
    ) -> "Demand":
        data = ya_models.DemandOfferBase(
            properties=properties,
            constraints=constraints,
        )
        return await cls.create(node, data)

    @classmethod
    async def create(cls, node: "GolemNode", data: ya_models.DemandOfferBase) -> "Demand":
        api = cls._get_api(node)
        demand_id = await api.subscribe_demand(data)
        return cls(node, demand_id)

    @api_call_wrapper(ignore=[404, 410])
    async def unsubscribe(self) -> None:
        self.set_no_more_children()
        await self.stop_collecting_events()
        await self.api.unsubscribe_demand(self.id)
        self.node.event_bus.emit(ResourceClosed(self))

    async def stop_collecting_events(self):
        if self._event_collecting_task is not None:
            self._event_collecting_task.cancel()
            self._event_collecting_task = None

    async def initial_proposals(self) -> AsyncIterator["Proposal"]:
        async for proposal in self.child_aiter():
            assert isinstance(proposal, Proposal)  # mypy
            if proposal.initial:
                yield proposal

    async def _process_yagna_events(self):
        event_collector = YagnaEventCollector(
            self.api.collect_offers,
            [self.id],
            {"timeout": 5, "max_events": 10},
        )
        async with event_collector:
            queue: asyncio.Queue = event_collector.event_queue()
            while True:
                event = await queue.get()
                if isinstance(event, ya_models.ProposalEvent):
                    proposal = Proposal.from_proposal_event(self.node, event)
                    parent = self._get_proposal_parent(proposal)
                    parent.add_child(proposal)
                elif isinstance(event, ya_models.ProposalRejectedEvent):
                    proposal = self.proposal(event.proposal_id)
                    proposal.add_event(event)

    def _get_proposal_parent(self, proposal: "Proposal") -> Union["Demand", "Proposal"]:
        if proposal.initial:
            parent = self
        else:
            parent_proposal_id = proposal.data.prev_proposal_id
            parent = Proposal(self.node, parent_proposal_id)  # type: ignore

            #   Sanity check - this should be true in all "expected" workflows,
            #   and we really want to detect any situation when it's not
            assert parent._parent is not None
        return parent

    def proposal(self, proposal_id: str) -> "Proposal":
        proposal = Proposal(self.node, proposal_id)

        #   NOTE: we don't know the parent, so we don't set it, but demand is known
        if proposal._demand is None:
            proposal.demand = self

        return proposal


class Proposal(MarketApiResource):
    _demand: Optional["Demand"] = None

    ##############################
    #   State-related properties
    @property
    def initial(self):
        assert self.data is not None
        return self.data.state == 'Initial'

    @property
    def draft(self):
        assert self.data is not None
        return self.data.state == 'Draft'

    @property
    def rejected(self):
        assert self.data is not None
        return self.data.state == 'Rejected'

    ###########################
    #   Tree-related methods
    @property
    def demand(self) -> "Demand":
        assert self._demand is not None
        return self._demand

    @demand.setter
    def demand(self, demand: "Demand") -> None:
        assert self._demand is None
        self._demand = demand

    @property
    def parent(self) -> Union["Proposal", "Demand"]:
        assert self._parent is not None
        assert isinstance(self._parent, Proposal) or isinstance(self._parent, Demand)  # mypy
        return self._parent

    @parent.setter
    def parent(self, parent: Union["Proposal", "Demand"]) -> None:
        assert self._parent is None
        self._parent = parent

        demand = parent if isinstance(parent, Demand) else parent.demand
        if self._demand is not None:
            assert self._demand is demand
        else:
            self.demand = demand

    def add_event(self, event: Union[ya_models.ProposalEvent, ya_models.ProposalRejectedEvent]) -> None:
        super().add_event(event)
        if isinstance(event, ya_models.ProposalRejectedEvent):
            self.set_no_more_children()

    async def responses(self) -> AsyncIterator["Proposal"]:
        async for child in self.child_aiter():
            if isinstance(child, Proposal):
                yield child

    ############################
    #   Negotiations
    @api_call_wrapper()
    async def create_agreement(self, autoclose=True, timeout: timedelta = timedelta(seconds=60)) -> "Agreement":
        proposal = ya_models.AgreementProposal(
            proposal_id=self.id,
            valid_to=datetime.now(timezone.utc) + timeout,  # type: ignore  # TODO: what is AgreementValidTo?
        )
        agreement_id = await self.api.create_agreement(proposal)
        agreement = Agreement(self.node, agreement_id)
        self.add_child(agreement)
        if autoclose:
            self.node.add_autoclose_resource(agreement)

        return agreement

    @api_call_wrapper()
    async def reject(self, reason: str = '') -> None:
        await self.api.reject_proposal_offer(
            self.demand.id, self.id, request_body={"message": reason}, _request_timeout=5
        )

    @api_call_wrapper()
    async def respond(self) -> "Proposal":
        data = await self._response_data()
        new_proposal_id = await self.api.counter_proposal_demand(self.demand.id, self.id, data, _request_timeout=5)

        new_proposal = type(self)(self.node, new_proposal_id)
        self.add_child(new_proposal)

        return new_proposal

    async def _response_data(self) -> ya_models.DemandOfferBase:
        # FIXME: this is a mock
        demand_data = await self.demand.get_data()
        data = ya_models.DemandOfferBase(properties=demand_data.properties, constraints=demand_data.constraints)
        return data

    ##########################
    #   Other
    @api_call_wrapper()
    async def _get_data(self) -> ya_models.Proposal:
        assert self.demand is not None
        data = await self.api.get_proposal_offer(self.demand.id, self.id)
        if data.state == "Rejected":
            self.set_no_more_children()
        return data

    @classmethod
    def from_proposal_event(cls, node: "GolemNode", event: ya_models.ProposalEvent) -> "Proposal":
        data = event.proposal
        assert data.proposal_id is not None  # mypy
        proposal = Proposal(node, data.proposal_id, data)
        proposal.add_event(event)
        return proposal


class Agreement(MarketApiResource):
    @api_call_wrapper()
    async def confirm(self) -> None:
        await self.api.confirm_agreement(self.id)

    @api_call_wrapper()
    async def wait_for_approval(self) -> bool:
        try:
            await self.api.wait_for_approval(self.id, timeout=15, _request_timeout=16)
            return True
        except exceptions.ApiException as e:
            if e.status == 410:
                return False
            elif e.status == 408:
                #   TODO: maybe this should be in api_call_wrapper?
                return await self.wait_for_approval()
            else:
                raise

    @api_call_wrapper()
    async def terminate(self, reason: str = ''):
        #   FIXME: check our state first
        await self.api.terminate_agreement(self.id, request_body={"message": reason})
        self.node.event_bus.emit(ResourceClosed(self))

    @property
    async def activity_possible(self) -> bool:
        #   FIXME
        return True
