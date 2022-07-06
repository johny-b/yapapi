from abc import ABC


class Event(ABC):
    pass


class ResourceEvent(ABC):
    def __init__(self, resource):
        self.resource = resource


class ResourceFound(ResourceEvent):
    """Emitted on a first contact of the GolemNode with some already existing resource.

    This happens:
    * When we find resource created by others, e.g. an Offer on the market.
    * When we find resource created by our node, but not in this run, e.g.
      await golem.allocation(allocation_id).load().
    """


class ResourceCreated(ResourceEvent):
    """Emitted when we create a new resource.

    E.g. on
    * await golem.create_allocation()
    * [TODO] when the Agreement is first created
    """


class ResourceChanged(ResourceEvent):
    """Emitted when we first notice a change in an already known resource.

    Change might be caused by us or by some party.
    Second argument is the old model (before the change), so comparing
    event.resource.data with event.old_data shows what changed.

    NULL (i.e. empty) change is not a change, even if we explicitly sent a resource-changing call.
    """

    def __init__(self, resource, old_data):
        super().__init__(resource)
        self.old_data = old_data


class ResourceDeleted(ResourceEvent):
    """Emitted when we discover than a resource was deleted.

    Usual case is when we delete a resource (e.g. await allocation.delete()),
    but this can be also emitted when some other party deletes (TODO: what? offer?).

    IOW, this is emitted whenever we start getting 404 instead of 200.
    """
