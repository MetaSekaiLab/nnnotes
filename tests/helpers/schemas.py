"""Validate synthetic product documents against the schemas shipped in nnnotes."""

from jsonschema import Draft202012Validator

from nnnotes import schemas


def validate(name: str, document: dict) -> None:
    Draft202012Validator(schemas.load(name)).validate(document)
