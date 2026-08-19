"""vlm-extraction-integrity.

Measures which defenses actually catch a wrong field value in VLM document
extraction, and shows that the defense teams reach for first, schema
validation, catches the errors that were never going to hurt them.

The package is importable without Pillow or the anthropic SDK; those are
needed only by vei.render and vei.models.ClaudeModel respectively.
"""

__version__ = "0.1.0"
