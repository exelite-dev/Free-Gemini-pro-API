"""
OmniBridge - Google Official API Proxy
A simple pass-through router to bypass sanctions for the official Google Gemini API.
"""
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
import httpx
import logging

router = APIRouter(prefix="/google", tags=["Google API Proxy"])
logger = logging.getLogger("omnibridge")

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def google_api_proxy(request: Request, path: str):
    """
    Acts as a transparent reverse-proxy for generativelanguage.googleapis.com.
    Useful for bypassing regional blocks (sanctions) when using an official API Key.
    """
    target_url = f"https://generativelanguage.googleapis.com/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"
        
    # Forward headers but remove host and content-length to avoid issues
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)
    
    async with httpx.AsyncClient(timeout=120) as client:
        body = await request.body()
        req = client.build_request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body
        )
        
        resp = await client.send(req, stream=True)
        
        async def stream_generator():
            async for chunk in resp.aiter_bytes():
                yield chunk
                
        # Forward the response back, stripping hop-by-hop headers
        resp_headers = {
            k: v for k, v in resp.headers.items() 
            if k.lower() not in ("content-encoding", "transfer-encoding", "connection")
        }
        
        return StreamingResponse(
            stream_generator(), 
            status_code=resp.status_code, 
            headers=resp_headers
        )
