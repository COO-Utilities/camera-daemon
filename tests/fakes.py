"""Test doubles shared by the chassis and frame tests."""

# pylint: disable=too-few-public-methods


class FakeRegistry:
    """Record what a keyword registry is asked to register, and read it back."""

    def __init__(self):
        self.registered = {}

    def _record(self, name, **kwargs):
        self.registered[name] = kwargs

    bool = int = float = string = trigger = _record

    def read(self, name):
        """Call a registered keyword's getter."""
        return self.registered[name]["getter"]()
