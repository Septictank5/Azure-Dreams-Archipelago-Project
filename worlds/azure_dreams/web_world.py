from BaseClasses import Tutorial
from worlds.AutoWorld import WebWorld


class AzureDreamsWebWorld(WebWorld):
    game = "Azure Dreams"
    theme = "stone"
    tutorials = [
        Tutorial(
            "Multiworld Setup Guide",
            "A guide to setting up Azure Dreams for Archipelago multiworld.",
            "English",
            "setup_en.md",
            "setup/en",
            ["ADAP Team"],
        )
    ]
