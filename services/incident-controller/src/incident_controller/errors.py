"""Errors shared by controller and privileged executor."""


class DockerControlError(Exception):
    """A Docker operation failed or its target was unsafe or ambiguous."""
