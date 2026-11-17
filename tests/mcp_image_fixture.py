"""Explicit image-protocol fixture: produces a synthetic pixel image, not a CAD screenshot."""
import io

from mcp.server.fastmcp import FastMCP, Image as McpImage
from PIL import Image

server = FastMCP('image-protocol-fixture-not-cad')


@server.tool()
def fixture_image() -> McpImage:
    output = io.BytesIO()
    Image.new('RGB', (64, 48), '#405060').save(output, format='PNG')
    return McpImage(data=output.getvalue(), format='png')


if __name__ == '__main__':
    server.run(transport='stdio')
