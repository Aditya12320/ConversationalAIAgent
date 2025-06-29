from typing import Union, Dict, Any, List
from langchain_core.caches import BaseCache
from langchain_core.callbacks import Callbacks, BaseCallbackManager
from langchain_core.messages import HumanMessage, AIMessage
from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint
from datetime import datetime, timedelta
from langchain_core.messages import SystemMessage
from huggingface_hub import login
import re
import json
import os
import dateparser
import logging
from pytz import timezone
import pytz

from langgraph.graph import Graph

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

HuggingFaceEndpoint.model_rebuild()
ChatHuggingFace.model_rebuild()

class BookingAgent:
    def __init__(self, calendar_service):
        self.calendar_service = calendar_service
        self.conversations = {}
        self.timezone = timezone('Asia/Kolkata')
        
        logger.info("Initializing BookingAgent...")

        # Get HuggingFace token from environment
        self.hf_token = os.getenv("HUGGINGFACEHUB_API_TOKEN")
        if not self.hf_token:
            logger.error("HUGGINGFACEHUB_API_TOKEN environment variable not set")
            raise ValueError("HUGGINGFACEHUB_API_TOKEN environment variable not set")

        try:
            from huggingface_hub import login
            login(token=self.hf_token, add_to_git_credential=False)
            
            self.llm = HuggingFaceEndpoint(
                repo_id="mistralai/Mistral-7B-Instruct-v0.3",
                task="text-generation",
                temperature=0.7,
                max_new_tokens=512,
                huggingfacehub_api_token=self.hf_token
            )
            self.chat = ChatHuggingFace(llm=self.llm)
        except Exception as e:
            logger.error(f"Failed to initialize LLM: {str(e)}")
            raise
        
        self.workflow = self._create_workflow()
        logger.info("BookingAgent initialized.")


    def _create_workflow(self):
        workflow = Graph()
        
        workflow.add_node("extract", self.extract_details)
        workflow.add_node("check", self.check_availability)
        workflow.add_node("suggest", self.suggest_slots)
        workflow.add_node("confirm", self.finalize_booking)
        workflow.add_node("respond", self.generate_response)
        workflow.add_node("inquire", self.generate_inquiry_response)
        
        workflow.set_entry_point("extract")
        
        workflow.add_conditional_edges(
            "extract",
            self._decide_after_extraction,
            {
                "check": "check",
                "respond": "respond",
                "inquire": "inquire"
            }
        )
        
        workflow.add_edge("inquire", "check")
        workflow.add_edge("check", "suggest")
        workflow.add_edge("suggest", "confirm")
        workflow.add_edge("confirm", "respond")
        workflow.add_edge("respond", "__end__")
        
        return workflow.compile()
    
    def generate_inquiry_response(self, state: Dict[str, Any]) -> Dict[str, Any]:
        details = state["conversation_state"].get("extracted_details", {})
        try:
            date_str = self._parse_date(details.get("date", ""))
            formatted_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %d")
            
            response = (
                f"Yes, I can check availability for {formatted_date}.\n"
                f"Would you like me to find a {details.get('duration', 30)}-minute slot "
                f"for a {details.get('purpose', 'meeting')}?"
            )
            
            state["conversation_state"]["messages"].append(AIMessage(content=response))
            return state
        except Exception as e:
            logger.error(f"Error generating inquiry response: {str(e)}")
            state["conversation_state"]["messages"].append(
                AIMessage(content="Sorry, I couldn't understand the date. Please try again.")
            )
            return state
    
    def _decide_after_extraction(self, state: Dict[str, Any]) -> str:
        user_input = state["user_input"].lower()
        details = state["conversation_state"].get("extracted_details", {})
        
        state["conversation_state"].pop("booking", None)
        
        is_booking = any(word in user_input for word in ["book", "schedule", "set up", "want"])
        is_inquiry = any(word in user_input for word in ["free", "available", "have time"])
        
        if is_booking and details.get("date") and details.get("time"):
            return "check"
        elif is_inquiry:
            return "inquire"
        elif details.get("date") and details.get("time"):
            return "check"
        else:
            return "respond"

    def process_message(self, message: str, user_id: str) -> str:
        if not user_id:
            logger.error("User ID cannot be None")
            return "Authentication error. Please sign in again."

        try:
            if user_id not in self.conversations:
                self.conversations[user_id] = {
                    "messages": [],
                    "extracted_details": {},
                    "state": "awaiting_input",
                    "available_slots": [],
                    "current_slot_index": 0
                }

            initial_state = {
                "user_id": user_id,
                "user_input": message,
                "conversation_state": self.conversations[user_id]
            }

            self.conversations[user_id]["messages"].append(HumanMessage(content=message))

            for step in self.workflow.stream(initial_state):
                pass

            last_message = next(
                (msg.content for msg in reversed(self.conversations[user_id]["messages"])
                if isinstance(msg, AIMessage)),
                "Sorry, I didn't understand that."
            )
            
            return last_message

        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            return "Sorry, I encountered an error. Please try again."

    def extract_details(self, state: Dict[str, Any]) -> Dict[str, Any]:
        conversation_state = state["conversation_state"]
        user_input = state.get("user_input", "")

        try:
            if self.chat:
                prompt = [
                    SystemMessage(content="""Extract appointment details as JSON with:
                    - intent (book/check/unsure)
                    - date (today/tomorrow/YYYY-MM-DD)
                    - time (HH:MM or description)
                    - duration (minutes)
                    - purpose (string)
                    
                    Examples:
                    {"intent": "book", "date": "next tuesday", "time": "14:00", "duration": 30, "purpose": "meeting"}
                    """ + user_input),
                    HumanMessage(content="Extract details in JSON only, no additional text.")
                ]

                response = self.chat.invoke(prompt)
                json_str = response.content.strip()
                
                if '```json' in json_str:
                    json_str = json_str.split('```json')[1].split('```')[0].strip()
                elif '```' in json_str:
                    json_str = json_str.split('```')[1].strip()
                elif '{' in json_str and '}' in json_str:
                    json_str = json_str[json_str.index('{'):json_str.rindex('}')+1]

                try:
                    details = json.loads(json_str)
                    details["date"] = details.get("date", "today")
                    details["time"] = details.get("time", "12:00")
                    details["duration"] = int(details.get("duration", 30))
                    details["purpose"] = details.get("purpose", "Meeting")

                    conversation_state["extracted_details"] = details
                except json.JSONDecodeError:
                    return self._simple_extraction(state)
                return {
                    "conversation_state": conversation_state,
                    "user_id": state["user_id"],
                    "user_input": state["user_input"]
                }
            else:
                return self._simple_extraction(state)
        except Exception:
            return self._simple_extraction(state)

    def _simple_extraction(self, state: Dict[str, Any]) -> Dict[str, Any]:
        conversation_state = state["conversation_state"]
        user_input = state.get("user_input", "").lower()
        
        extracted = {
            "date": "today",
            "time": "12:00",
            "duration": 30,
            "purpose": "Meeting"
        }
        
        if "tomorrow" in user_input:
            extracted["date"] = "tomorrow"
        elif any(day in user_input for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]):
            extracted["date"] = "next " + [d for d in ["monday", "tuesday", "wednesday", "thursday", "friday"] 
                                        if d in user_input][0]
        elif "next week" in user_input:
            extracted["date"] = "next week"
        
        if "afternoon" in user_input:
            extracted["time"] = "14:00"
        elif "morning" in user_input:
            extracted["time"] = "09:00"
        elif "evening" in user_input:
            extracted["time"] = "17:00"
        else:
            time_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", user_input)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2) or 0)
                period = time_match.group(3)
                
                if period == 'pm' and hour < 12:
                    hour += 12
                elif period == 'am' and hour == 12:
                    hour = 0
                extracted["time"] = f"{hour:02d}:{minute:02d}"
        
        if "between" in user_input and ("-" in user_input or "and" in user_input):
            range_match = re.search(
                r"between (\d+)(?::(\d+))?\s*(am|pm)?\s*(?:-|and)\s*(\d+)(?::(\d+))?\s*(am|pm)?", 
                user_input
            )
            if range_match:
                start_hour = int(range_match.group(1))
                start_min = int(range_match.group(2) or 0)
                start_period = range_match.group(3)
                end_hour = int(range_match.group(4))
                end_min = int(range_match.group(5) or 0)
                end_period = range_match.group(6)
                
                if start_period == 'pm' and start_hour < 12:
                    start_hour += 12
                elif start_period == 'am' and start_hour == 12:
                    start_hour = 0
                
                if end_period == 'pm' and end_hour < 12:
                    end_hour += 12
                elif end_period == 'am' and end_hour == 12:
                    end_hour = 0
                
                extracted["time"] = f"{start_hour:02d}:{start_min:02d}-{end_hour:02d}:{end_min:02d}"
                extracted["duration"] = (end_hour * 60 + end_min) - (start_hour * 60 + start_min)
        
        conversation_state["extracted_details"] = extracted
        return {
            "conversation_state": conversation_state,
            "user_id": state["user_id"],
            "user_input": state["user_input"]
        }

    def check_availability(self, state: Dict[str, Any]) -> Dict[str, Any]:
        conversation_state = state.get("conversation_state", {})
        user_id = state.get("user_id")

        if not user_id:
            conversation_state.setdefault("messages", []).append(
                AIMessage(content="Authentication error. Please sign in again.")
            )
            return {"conversation_state": conversation_state}

        try:
            details = conversation_state.get("extracted_details", {})
            if not details:
                raise ValueError("No booking details extracted")

            date_str = self._parse_date(details["date"])
            start_time, end_time = self._parse_time(details["time"])
            duration = int(details.get("duration", 30))

            all_slots = self.calendar_service.get_available_slots(user_id, date_str, duration)
            
            filtered_slots = [
                slot for slot in all_slots
                if self._is_time_in_range(slot["start"], start_time, end_time)
            ]

            conversation_state.update({
                "available_slots": filtered_slots or all_slots,
                "current_slot_index": 0
            })

            return {
                "conversation_state": conversation_state,
                "user_id": user_id,
                "user_input": state.get("user_input")
            }

        except Exception as e:
            logger.error(f"check_availability failed: {str(e)}")
            conversation_state.setdefault("messages", []).append(
                AIMessage(content="Failed to check availability. Please try again.")
            )
            return {
                "conversation_state": conversation_state,
                "user_id": user_id,
                "user_input": state.get("user_input")
            }

    def _is_time_in_range(self, slot_time: str, start: str, end: str) -> bool:
        slot_hour = int(slot_time.split("T")[1].split(":")[0])
        start_hour = int(start.split(":")[0])
        end_hour = int(end.split(":")[0])
        return start_hour <= slot_hour < end_hour

    def suggest_slots(self, state: Dict[str, Any]) -> Dict[str, Any]:
        conversation_state = state["conversation_state"]
        available_slots = conversation_state.get("available_slots", [])
        idx = conversation_state.get("current_slot_index", 0)
        
        if not available_slots:
            conversation_state["messages"].append(
                AIMessage(content="No available slots found for your requested time. Please try another time.")
            )
            return {
                "conversation_state": conversation_state,
                "user_id": state["user_id"],
                "user_input": state["user_input"]
            }

        if idx < len(available_slots):
            slot = available_slots[idx]
            response = (
                f"I found an available slot:\n"
                f"📅 {self._format_datetime(slot['start'])} - {self._format_datetime(slot['end'])}\n"
                f"Would this work for you? (Yes/No)"
            )
            
            conversation_state.update({
                "suggested_slot": slot,
                "current_slot_index": idx + 1,
                "messages": [AIMessage(content=response)]
            })
        else:
            conversation_state["messages"].append(
                AIMessage(content="No more available slots for your requested time. Please try another time.")
            )
        
        return {
            "conversation_state": conversation_state,
            "user_id": state["user_id"],
            "user_input": state["user_input"]
        }

    def finalize_booking(self, state: Dict[str, Any]) -> Dict[str, Any]:
        conversation_state = state["conversation_state"]
        user_id = state.get("user_id")
        
        if not user_id:
            conversation_state["messages"].append(
                AIMessage(content="Authentication error. Please sign in again.")
            )
            return {
                "conversation_state": conversation_state,
                "user_id": state["user_id"],
                "user_input": state["user_input"]
            }
            
        if "suggested_slot" not in conversation_state:
            conversation_state["messages"].append(
                AIMessage(content="No appointment slot selected. Please start over.")
            )
            return {
                "conversation_state": conversation_state,
                "user_id": state["user_id"],
                "user_input": state["user_input"]
            }
            
        try:
            slot = conversation_state["suggested_slot"]
            details = conversation_state.get("extracted_details", {})
            
            booking = self.calendar_service.book_appointment(
                user_id,
                slot["start"],
                slot["end"],
                details.get("purpose", "Meeting")
            )
            
            conversation_state["booking"] = booking
            return {
                "conversation_state": conversation_state,
                "user_id": state["user_id"],
                "user_input": state["user_input"]
            }
            
        except Exception as e:
            logger.error(f"Booking failed: {str(e)}")
            conversation_state["messages"].append(
                AIMessage(content="Failed to book appointment. Please try again.")
            )
            return {
                "conversation_state": conversation_state,
                "user_id": state["user_id"],
                "user_input": state["user_input"]
            }

    def generate_response(self, state: Dict[str, Any]) -> Dict[str, Any]:
        conv_state = state["conversation_state"]
        
        if "booking" in conv_state:
            booking = conv_state["booking"]
            response = (
                "✅ Appointment booked!\n"
                f"📅 When: {self._format_datetime(booking['start'])} - "
                f"{self._format_datetime(booking['end'])}\n"
                f"🔗 Link: {booking['htmlLink']}"
            )
        elif not conv_state.get("available_slots"):
            response = "No available slots found for your requested time."
        else:
            response = "How would you like to proceed?"
        
        if not any(msg.content == response for msg in conv_state["messages"]):
            conv_state["messages"].append(AIMessage(content=response))
        
        return state
    
    def _parse_time(self, time_str: str) -> tuple[str, str]:
        """Parse natural language times into (start, end) format"""
        if not time_str:
            return ("09:00", "17:00")

        time_str = time_str.lower().strip()
        
        # Standardize separators
        time_str = re.sub(r"\b(to|until|through|thru|–|—)\b", "-", time_str)

        # Named time periods
        named_times = {
            "morning": ("09:00", "12:00"),
            "afternoon": ("13:00", "17:00"),
            "evening": ("17:00", "20:00"),
            "night": ("20:00", "23:00"),
            "noon": ("12:00", "13:00"),
            "midnight": ("00:00", "01:00"),
        }

        if time_str in named_times:
            return named_times[time_str]

        # Handle "from X to Y" format
        if "from" in time_str and "to" in time_str:
            parts = time_str.split("to")
            start = parts[0].replace("from", "").strip()
            end = parts[1].strip()
            return (self._normalize_time(start), self._normalize_time(end))

        # Handle duration specifications
        duration_match = re.search(r"for (\d+)\s*(hour|hr|minute|min)", time_str)
        if duration_match:
            time_part = time_str[:duration_match.start()].strip()
            num = int(duration_match.group(1))
            unit = duration_match.group(2)
            minutes = num * 60 if unit.startswith("h") else num
            return (self._normalize_time(time_part), 
                    self._add_minutes(self._normalize_time(time_part), minutes))

        # Handle ranges
        if "-" in time_str:
            start, end = time_str.split("-", 1)
            return (self._normalize_time(start.strip()), 
                    self._normalize_time(end.strip()))

        # Single time
        normalized = self._normalize_time(time_str)
        return (normalized, self._add_minutes(normalized, 30))



    def _normalize_time(self, time_str: str) -> str:
        time_str = time_str.lower().replace(".", "").strip()
        is_pm = "pm" in time_str
        time_str = time_str.replace("am", "").replace("pm", "").strip()
        
        if ":" not in time_str:
            hour = int(time_str)
            if is_pm and hour < 12:
                hour += 12
            return f"{hour:02d}:00"
        
        hour, minute = time_str.split(":")
        hour = int(hour)
        if is_pm and hour < 12:
            hour += 12
        return f"{hour:02d}:{minute}"

    def _add_minutes(self, time_str: str, minutes: int) -> str:
        hour, minute = map(int, time_str.split(":"))
        total_minutes = hour * 60 + minute + minutes
        return f"{total_minutes//60:02d}:{total_minutes%60:02d}"
        
    def _parse_date(self, date_str: str) -> str:
        """Parse natural language dates into YYYY-MM-DD format"""
        today = datetime.now(self.timezone).date()

        if not date_str:
            return today.strftime("%Y-%m-%d")

        date_str = date_str.lower().strip()

        # Common relative phrases
        relative_keywords = {
            "today": 0,
            "tomorrow": 1,
            "day after tomorrow": 2,
            "next week": 7,
            "next month": 30,
        }

        if date_str in relative_keywords:
            target_date = today + timedelta(days=relative_keywords[date_str])
            return target_date.strftime("%Y-%m-%d")

        # Weekday references
        weekdays = {
            "monday": 0, "tuesday": 1, "wednesday": 2,
            "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6
        }

        for prefix in ["next", "this", "coming"]:
            for day, idx in weekdays.items():
                if f"{prefix} {day}" in date_str:
                    days_ahead = (idx - today.weekday() + (7 if prefix != "this" else 0)) % 7
                    if days_ahead == 0 and prefix != "this":
                        days_ahead = 7
                    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        # Natural language parsing
        try:
            parsed = dateparser.parse(
                date_str,
                settings={
                    "TIMEZONE": str(self.timezone),
                    "RETURN_AS_TIMEZONE_AWARE": False,
                    "PREFER_DATES_FROM": "future"
                }
            )
            if parsed:
                return parsed.date().strftime("%Y-%m-%d")
        except Exception as e:
            logger.warning(f"Dateparser failed for '{date_str}': {str(e)}")

        raise ValueError(f"Invalid date format: '{date_str}'. Please use natural expressions or YYYY-MM-DD.")

    def _format_datetime(self, iso_str: str) -> str:
        try:
            dt = datetime.fromisoformat(iso_str.replace('Z', ''))
            if dt.tzinfo is None:
                dt = self.timezone.localize(dt)
            return dt.strftime("%a %b %d, %I:%M %p")
        except ValueError:
            return iso_str