import asyncio
from typing import Iterable, Tuple
from datetime import datetime, timedelta, timezone

from yapapi import rest
from yapapi.engine import DEFAULT_DRIVER, DEFAULT_NETWORK, DEFAULT_SUBNET
from yapapi.payload import Payload
from yapapi.props.builder import DemandBuilder
from yapapi import props

from .payment import Allocation
from .market import Demand, Offer, Agreement


class GolemNode:
    def __init__(self):
        self._api_config = rest.Configuration()
        self.payment_driver = DEFAULT_DRIVER
        self.payment_network = DEFAULT_NETWORK
        self.subnet = DEFAULT_SUBNET

    ########################
    #   Start/stop interface
    async def __aenter__(self):
        await self.start()

    async def __aexit__(self, *exc_info):
        await self.stop()

    async def start(self):
        self._ya_market_api = self._api_config.market()
        self._ya_activity_api = self._api_config.activity()
        self._ya_payment_api = self._api_config.payment()
        self._ya_net_api = self._api_config.net()

    async def stop(self):
        await asyncio.gather(
            self._ya_market_api.close(),
            self._ya_activity_api.close(),
            self._ya_payment_api.close(),
            self._ya_net_api.close(),
        )

    ###########################
    #   Create new objects
    async def create_allocation(self, amount: float) -> Allocation:
        #   TODO: This creates an Allocation for a matching requestor account
        #         (or raises an exception if there is none).
        #         Can we have more than a single matching account?
        #         If yes - how to approach this? Add `create_allocations`?
        return await Allocation.create_any_account(self, amount)

    async def create_demand(self, payload: Payload, allocations: Iterable[Allocation] = ()) -> Demand:
        builder = DemandBuilder()
        await builder.decorate(payload)
        self._add_builder_params(builder)
        await self._add_builder_allocations(builder, allocations)
        return await Demand.create_from_properties_constraints(self, builder.properties, builder.constraints)

    def _add_builder_params(self, builder: DemandBuilder) -> None:
        expiration_time = datetime.now(timezone.utc) + timedelta(seconds=1800)
        builder.add(props.Activity(expiration=expiration_time, multi_activity=True))
        builder.add(props.NodeInfo(subnet_tag=self.subnet))

    async def _add_builder_allocations(self, builder: DemandBuilder, allocations: Iterable[Allocation]) -> None:
        for allocation in allocations:
            properties, constraints = await allocation.demand_properties_constraints()
            for constraint in constraints:
                builder.ensure(constraint)

            #   TODO: what if we already have such properties in builder?
            #         E.g. we have different address in allocations?
            #         (Now this should work just as in `yapapi.Engine`, but there we
            #         can't have different addresses I guess?)
            builder.properties.update({p.key: p.value for p in properties})

    ###########################
    #   Single-object factories for already existing objects
    #   TODO: decide - should we have a caching logic here and return always the same
    #         object for the same ID? I'd say: yes, but maybe not really?
    def allocation(self, allocation_id) -> Allocation:
        return Allocation(self, allocation_id)

    def demand(self, demand_id) -> Demand:
        return Demand(self, demand_id)

    def offer(self, offer_id) -> Offer:
        return Offer(self, offer_id)

    def agreement(self, agreement_id) -> Agreement:
        return Agreement(self, agreement_id)

    ##########################
    #   Multi-object factories for already existing objects
    async def allocations(self) -> Tuple[Allocation]:
        return await Allocation.get_all(self)

    async def demands(self) -> Tuple[Demand]:
        return await Demand.get_all(self)

    #########
    #   Other
    def __str__(self):
        lines = [
            f"{type(self).__name__}(",
            f"  app_key = {self._api_config.app_key},",
            f"  subnet = {self.subnet},",
            f"  payment_driver = {self.payment_driver},",
            f"  payment_network = {self.payment_network},",
            f"  market_url = {self._api_config.market_url},",
            f"  payment_url = {self._api_config.payment_url},",
            f"  activity_url = {self._api_config.activity_url},",
            f"  net_url = {self._api_config.net_url},",
            f"  gsb_url = TODO? this is used by yagna only, but is part of the config?",
            f")",
        ]
        return "\n".join(lines)
