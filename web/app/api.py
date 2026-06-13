"""API endpoints for serving files."""
import os
from pathlib import Path
from starlette.responses import FileResponse, Response
from fastapi import HTTPException


def get_stock_file(stock_code: str, file_type: str):
    """
    Serve stock analysis files from /tmp directory.
    
    Args:
        stock_code: Stock code (e.g., "000006")
        file_type: Type of file to serve ("report" or "cmd")
    """
    # Map file types to actual filenames
    file_map = {
        "report": "report.html",
        "cmd": "cmd.md"
    }
    
    if file_type not in file_map:
        raise HTTPException(status_code=400, detail="Invalid file type")
    
    filename = file_map[file_type]
    file_path = Path(f"/tmp/{stock_code}/{filename}")
    
    # Check if file exists
    if not file_path.exists():
        raise HTTPException(
            status_code=404, 
            detail=f"File not found: {file_path}"
        )
    
    # Determine content type
    content_type = "text/html" if file_type == "report" else "text/markdown"
    
    # Return file response
    return FileResponse(
        path=str(file_path),
        media_type=content_type,
        filename=filename
    )
