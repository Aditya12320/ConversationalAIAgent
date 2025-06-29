

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from auth.router import router as auth_router
from pydantic import BaseModel, Field, field_validator
from backend.agent import BookingAgent
from backend.calendar_service import GoogleCalendarService
import uvicorn
import logging
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware
from dotenv import load_dotenv
import os

# Load environment variables first
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.include_router(auth_router, prefix="/api")  # Remove the extra /auth

# Rate limiting
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize services
calendar_service = GoogleCalendarService()
booking_agent = BookingAgent(calendar_service)

class UserMessage(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    user_id: str = Field(..., min_length=1, max_length=50)
    
    @field_validator('message')
    def sanitize_message(cls, v):
        v = v.replace('<', '&lt;').replace('>', '&gt;')
        return v.strip()

@app.get("/auth/status")
async def auth_status(user_id: str):
    try:
        creds = calendar_service._get_credentials(user_id)
        return {"authenticated": bool(creds)}
    except Exception as e:
        logger.error(f"Error checking auth status: {str(e)}")
        return {"authenticated": False, "error": str(e)}

@app.post("/chat")
@limiter.limit("10/minute")
async def chat(request: Request, user_message: UserMessage):
    try:
        if not calendar_service._get_credentials(user_message.user_id):
            raise HTTPException(
                status_code=401,
                detail="Please authenticate with Google Calendar first"
            )
        
        response = booking_agent.process_message(
            user_message.message, 
            user_message.user_id
        )
        return {"response": response}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in chat endpoint: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while processing your request"
        )

@app.get("/available-slots")
@limiter.limit("10/minute")
async def get_available_slots(request: Request, user_id: str, date: str, duration: int = 30):
    try:
        slots = calendar_service.get_available_slots(user_id, date, duration)
        return {"slots": slots}
    except Exception as e:
        logger.error(f"Error fetching available slots: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)