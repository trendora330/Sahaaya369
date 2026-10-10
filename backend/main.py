import os
import httpx
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict

app = FastAPI(title="Singularity Accessible Dining API")

# Enable CORS for Vite / Next.js local frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ASR_ENDPOINT = os.getenv(
    "IIITH_ASR_ENDPOINT",
    "https://canvas.iiit.ac.in/sandboxbeprod/infer_asr/6979e11150f13fc15e603531",
)

# In-memory store for table orders and staff dialogs
ORDERS_DB: Dict[str, List[dict]] = {"table_1": []}
MESSAGES_DB: List[dict] = []


# --- Schemas ---

class InferRequest(BaseModel):
    prompt: str
    condition: Optional[str] = "default"
    language: Optional[str] = "en"

class OrderItem(BaseModel):
    id: str
    name: str
    price: float
    quantity: int

class StaffMessage(BaseModel):
    sender: str  # "staff" or "diner"
    text: str
    table: Optional[str] = "table_1"


# --- Heuristic Condition Detector & Intent Engine ---

def analyze_intent_and_condition(prompt: str):
    text = prompt.lower()
    inferred_condition = None
    reason = None

    # Condition inference
    if any(k in text for k in ["blind", "cant see", "can't see", "read to me", "read out", "describe"]):
        inferred_condition = "visual"
        reason = "Visual accessibility cues detected in request"
    elif any(k in text for k in ["deaf", "cant hear", "can't hear", "subtitles", "captions", "mute"]):
        inferred_condition = "hearing"
        reason = "Hearing or captioning requirement detected"
    elif any(k in text for k in ["slow down", "hard to understand", "too small", "confused", "elderly"]):
        inferred_condition = "ageing"
        reason = "Simplified pacing or visual layout requested"

    # Action intent detection
    intent = "general_query"
    if any(k in text for k in ["waiter", "staff", "call", "help"]):
        intent = "call_staff"
    elif any(k in text for k in ["bill", "check", "pay", "total"]):
        intent = "request_bill"
    elif any(k in text for k in ["veg", "vegetarian", "jain", "spicy"]):
        intent = "filter_menu"
    elif any(k in text for k in ["order", "add", "paneer", "chicken", "dal"]):
        intent = "modify_order"

    return intent, inferred_condition, reason


# --- Endpoints ---

@app.post("/api/asr")
async def speech_to_text(audio: UploadFile = File(...)):
    """
    Receives raw audio recorded by the frontend and proxies it
    directly to the IIITH sandbox ASR endpoint.
    """
    try:
        audio_bytes = await audio.read()
        files = {"file": (audio.filename or "audio.wav", audio_bytes, audio.content_type or "audio/wav")}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(ASR_ENDPOINT, files=files)

        if response.status_code == 200:
            result = response.json()
            # Standardize response structure based on sandbox keys
            text_transcribed = (
                result.get("text")
                or result.get("transcript")
                or result.get("output")
                or ""
            )
            return {"status": "success", "text": text_transcribed, "raw": result}
        else:
            # Fallback for hackathon testing if the sandbox endpoint requires specific auth or format
            return {
                "status": "warning",
                "text": "Simulated speech transcript: Please show mild veg curries.",
                "upstream_status": response.status_code,
            }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "text": "Simulated fallback: Add Paneer Butter Masala to order.",
        }


@app.post("/api/organizer-llm")
async def process_llm_query(req: InferRequest):
    """
    Processes user input, infers disability profiles, and formats
    accessible output strings (with step-by-step simplification if needed).
    """
    intent, inferred_condition, reason = analyze_intent_and_condition(req.prompt)
    effective_condition = inferred_condition or req.condition

    # Dynamic response generation based on the active accessibility profile
    if effective_condition == "visual":
        reply = (
            f"Descriptive overview: {req.prompt}. "
            "The top recommendation is Paneer Butter Masala at 280 rupees, mild gravy, safe for vegetarians."
        )
    elif effective_condition == "ageing":
        reply = (
            "Step 1: I have reviewed the menu for you. "
            "Step 2: Paneer Butter Masala is mild and soft. "
            "Step 3: Press the green Add button below to select it."
        )
    elif intent == "call_staff":
        reply = "Table staff notified. A waiter is on their way with assistance."
    elif intent == "request_bill":
        reply = "Your bill breakdown is prepared in the summary tab."
    else:
        reply = f"Understood: '{req.prompt}'. Displaying matching dishes on your screen."

    return {
        "reply": reply,
        "intent": intent,
        "inferred_condition": inferred_condition,
        "inference_reason": reason,
    }


@app.post("/api/orders/update")
async def update_cart(items: List[OrderItem], table_id: str = "table_1"):
    """
    Updates the table cart so the staff terminal sees live selections.
    """
    ORDERS_DB[table_id] = [item.dict() for item in items]
    return {"status": "ok", "items_count": len(items)}


@app.get("/api/orders/{table_id}")
async def get_cart(table_id: str):
    """
    Endpoint consumed by the `/staff` route to display active orders.
    """
    return {"table_id": table_id, "items": ORDERS_DB.get(table_id, [])}


@app.post("/api/staff/message")
async def post_staff_message(msg: StaffMessage):
    """
    Sends messages between staff and diners with live sync.
    """
    entry = {"sender": msg.sender, "text": msg.text, "table": msg.table}
    MESSAGES_DB.append(entry)
    return {"status": "sent", "entry": entry}


@app.get("/api/staff/messages")
async def get_messages():
    return {"messages": MESSAGES_DB}