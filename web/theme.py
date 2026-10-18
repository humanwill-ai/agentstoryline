"""Apply the viewer's initial theme before the browser can paint the page."""


def apply_default_flow_theme(template):
    """Return a flow template whose first frame is already Spotlight."""
    root = '<main id="tf-flow" class="f-single">'
    spotlight = '<option value="spotlight">Spotlight</option>'
    if template.count(root) != 1 or template.count(spotlight) != 1:
        raise ValueError("flow template is missing its default-theme markers")
    return template.replace(
        root, '<main id="tf-flow" class="f-single f-spotlight">', 1
    ).replace(
        spotlight, '<option value="spotlight" selected>Spotlight</option>', 1
    )
