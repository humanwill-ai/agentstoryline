"""Embed fixed local brand assets before inserting untrusted recording content."""
import base64
from pathlib import Path


def embed_brand_assets(template):
    assets = Path(__file__).resolve().parent / 'assets'
    for placeholder, filename in (('__AGENTSTORYLINE_LOGO__', 'sleepy-replicant-128.png'),
                                  ('__AGENTSTORYLINE_FAVICON__', 'sleepy-replicant-32.png')):
        uri = 'data:image/png;base64,' + base64.b64encode((assets / filename).read_bytes()).decode('ascii')
        template = template.replace(placeholder, uri)
    return template
