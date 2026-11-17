"""Frozen entry point; Blender and uv/Python are not required for MCP initialization."""
import multiprocessing

if __name__ == '__main__':
    multiprocessing.freeze_support()
    from blender_mcp.server import main
    main()
