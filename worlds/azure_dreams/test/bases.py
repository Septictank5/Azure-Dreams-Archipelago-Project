from test.bases import WorldTestBase

from ..world import AzureDreamsWorld


class AzureDreamsTestBase(WorldTestBase):
    game = "Azure Dreams"
    world: AzureDreamsWorld
