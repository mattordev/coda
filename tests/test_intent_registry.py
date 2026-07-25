import unittest

from ai.intents import Intent, IntentRegistry


class IntentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = IntentRegistry()
        self.intent = Intent(
            name="maps",
            description="Search for a place.",
            aliases=("map", "navigate"),
        )

    def test_registers_and_gets_intent_by_name(self):
        self.registry.register(self.intent)

        result = self.registry.get("maps")

        self.assertIs(result, self.intent)

    def test_gets_intent_by_normalized_alias(self):
        self.registry.register(self.intent)

        result = self.registry.get(" NAVIGATE ")

        self.assertIs(result, self.intent)

    def test_gets_intent_by_normalized_example(self):
        intent = Intent(
            name="time",
            description="Report the current time.",
            examples=("What's the time?",),
        )
        self.registry.register(intent)

        result = self.registry.get_by_example(
            "  WHAT'S   THE TIME!  "
        )

        self.assertIs(result, intent)

    def test_returns_none_for_unknown_name(self):
        self.assertIsNone(self.registry.get("unknown"))

    def test_rejects_duplicate_intent_name(self):
        self.registry.register(self.intent)

        duplicate = Intent(
            name=" MAPS ",
            description="Duplicate maps intent.",
        )

        with self.assertRaisesRegex(ValueError, "already registered"):
            self.registry.register(duplicate)

    def test_rejects_duplicate_aliases_after_normalization(self):
        intent = Intent(
            name="say",
            description="Repeat some text.",
            aliases=("repeat", " REPEAT "),
        )

        with self.assertRaisesRegex(ValueError, "duplicate alias"):
            self.registry.register(intent)

    def test_rejects_duplicate_examples_after_normalization(self):
        intent = Intent(
            name="time",
            description="Report the current time.",
            examples=("Current time.", " CURRENT   TIME! "),
        )

        with self.assertRaisesRegex(ValueError, "duplicate example"):
            self.registry.register(intent)

    def test_rejects_example_registered_by_another_intent(self):
        self.registry.register(
            Intent(
                name="time",
                description="Report the current time.",
                examples=("Tell me the time.",),
            )
        )
        conflicting = Intent(
            name="clock",
            description="Report the clock.",
            examples=(" TELL ME THE TIME! ",),
        )

        with self.assertRaisesRegex(ValueError, "already registered"):
            self.registry.register(conflicting)

    def test_rejects_alias_that_matches_existing_intent_name(self):
        self.registry.register(self.intent)

        conflicting = Intent(
            name="directions",
            description="A conflicting intent.",
            aliases=("maps",),
        )

        with self.assertRaisesRegex(ValueError, "already registered"):
            self.registry.register(conflicting)

    def test_rejects_name_that_matches_existing_alias(self):
        self.registry.register(self.intent)

        conflicting = Intent(
            name="navigate",
            description="A conflicting intent.",
        )

        with self.assertRaisesRegex(ValueError, "already registered"):
            self.registry.register(conflicting)

    def test_failed_registration_does_not_partially_modify_registry(self):
        self.registry.register(self.intent)

        conflicting = Intent(
            name="connected",
            description="Check internet connectivity.",
            aliases=("online", "map"),
            examples=("Are we connected?",),
        )

        with self.assertRaises(ValueError):
            self.registry.register(conflicting)

        self.assertIsNone(self.registry.get("connected"))
        self.assertIsNone(self.registry.get("online"))
        self.assertIsNone(
            self.registry.get_by_example("Are we connected?")
        )

if __name__ == "__main__":
    unittest.main()
