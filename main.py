import os
import time
import subprocess
import json
import math
import pyautogui
import threading
import base64
import customtkinter as ctk
from PIL import Image

# macOS Native OCR
import Quartz
import Vision
from AppKit import NSImage

# Import models
import anthropic
import dashscope
from dashscope import MultiModalConversation

PROMPT = """
You are AIcceptor, a security agent monitoring an AI coding assistant named 'Antigravity'.
Your job is to look at the provided screenshot of the user's IDE/screen and determine if Antigravity is asking the user to accept a code change, run a terminal command, or allow an action.

Analyze the screenshot carefully and decide if the proposed action is SAFE or UNSAFE based on these absolute rules:

**SAFE ACTIONS:**
- Edits made to source code files within the current project.
- Reading files within the current project.
- Running standard build or test commands (e.g., `npm run dev`, `npm test`, `pytest`, `cargo build`).
- Standard file creation/deletion *within* the project directory.
- Standard git commands on the current repository.

**UNSAFE ACTIONS (Require User Review):**
- System-level commands (e.g., `rm -rf /`, formatting disks, changing system configs).
- Modifying files outside the current project structure (e.g., editing `~/.bashrc`, changing global settings).
- Installing global dependencies that look suspicious.
- Hallucinations (making up non-existent files or directories).
- Any action you cannot confidently determine is safe.

Analyze the screen. Determine the status.
If you find a prompt from Antigravity asking to "Accept", "Allow", or "Accept all", and you determine it is SAFE, you MUST provide the exact X and Y coordinates (in pixels) of the center of that specific button so I can click it. Be extremely precise.
If there is an "Accept all" button visible (usually when there are multiple actions), you MUST provide the coordinates for the "Accept all" button, not the individual "Accept" buttons.

Respond strictly in the following JSON format:
{
  "status": "SAFE" | "UNSAFE" | "NONE",
  "reason": "Brief explanation of your decision (e.g., 'Modifying standard project file index.js', 'Attempting to run unsafe command sudo rm -rf')",
  "button_coordinates": {
    "x": <integer or null>,
    "y": <integer or null>
  }
}

Return ONLY valid JSON.
"""

def check_local_ocr(image_path):
    """Uses macOS Vision framework to scan for 'Accept' or 'Allow' instantly. Returns (is_detected, buttons_list)."""
    try:
        ns_image = NSImage.alloc().initWithContentsOfFile_(image_path)
        if not ns_image:
            return False, []
        cg_image = ns_image.CGImageForProposedRect_context_hints_(None, None, None)[0]
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate) 
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
        success, _ = handler.performRequests_error_([request], None)
        if not success:
            return False, []
            
        screen_w, screen_h = pyautogui.size()
        found_buttons = []
        is_detected = False
        
        for observation in request.results():
            candidate = observation.topCandidates_(1).firstObject()
            if candidate:
                text = candidate.string().lower()
                # UI buttons are short (e.g. "Accept", "Accept 2 Files", "Accept all")
                # Source code lines containing the word "accept" will be long.
                if ("accept" in text or "allow" in text) and len(text) < 30:
                    # Ignore the AIcceptor app's own text logs
                    if "aicceptor" in text:
                        continue
                    
                    is_detected = True
                    bbox = observation.boundingBox()
                    x = (bbox.origin.x + bbox.size.width / 2.0) * screen_w
                    y = (1.0 - (bbox.origin.y + bbox.size.height / 2.0)) * screen_h
                    found_buttons.append({"text": text, "x": x, "y": y})
                    
        return is_detected, found_buttons
    except Exception as e:
        print(f"OCR Error: {e}")
        return True, [] # Fail open so it still tries the API if OCR crashes



def take_screenshot(filename="/tmp/aicceptor_screen.png"):
    """Takes a screenshot using native macOS utility."""
    # -x mutes the sound, -C includes the cursor, -m main monitor only
    subprocess.run(["screencapture", "-x", "-m", "-C", filename], check=True)
    return filename

def notify_user(message, title="AIcceptor Alert"):
    """Sends a native macOS notification."""
    script = f'display notification "{message}" with title "{title}" sound name "Basso"'
    subprocess.run(["osascript", "-e", script])

def encode_image_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def call_gemini(image_path, api_key):
    from google import genai
    client = genai.Client(api_key=api_key)
    img = Image.open(image_path)
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[PROMPT, img]
    )
    return response.text.strip()

def call_claude(image_path, api_key):
    client = anthropic.Anthropic(api_key=api_key)
    base64_image = encode_image_base64(image_path)
    message = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64_image,
                        },
                    },
                    {"type": "text", "text": PROMPT}
                ],
            }
        ],
    )
    return message.content[0].text

def call_qwen(image_path, api_key):
    dashscope.api_key = api_key
    messages = [
        {
            "role": "user",
            "content": [
                {"image": f"file://{image_path}"},
                {"text": PROMPT}
            ]
        }
    ]
    response = MultiModalConversation.call(model='qwen-vl-max', messages=messages)
    if response.status_code == 200:
        return response.output.choices[0].message.content[0].get('text', '')
    else:
        raise Exception(f"Qwen error: {response.code} {response.message}")

class AIcceptorApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("AIcceptor")
        self.geometry("450x520")
        self.resizable(False, False)
        
        # State
        self.running = False
        self.monitor_thread = None
        self.last_action_time = 0
        
        # UI Elements
        self.title_label = ctk.CTkLabel(self, text="AIcceptor", font=ctk.CTkFont(size=24, weight="bold"))
        self.title_label.pack(pady=(20, 5))

        # Regime selection
        self.regime_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.regime_frame.pack(fill="x", padx=20, pady=(0, 8))
        self.regime_label = ctk.CTkLabel(self.regime_frame, text="Regime:")
        self.regime_label.pack(side="left")
        self.regime_var = ctk.StringVar(value="Safe")
        self.regime_btn = ctk.CTkSegmentedButton(
            self.regime_frame,
            values=["Safe", "Dangerous"],
            variable=self.regime_var,
            command=self._on_regime_change
        )
        self.regime_btn.pack(side="right", fill="x", expand=True, padx=(10, 0))
        
        # Model Selection
        self.model_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.model_frame.pack(fill="x", padx=20, pady=5)
        self.model_label = ctk.CTkLabel(self.model_frame, text="Select Model:")
        self.model_label.pack(side="left")
        self.model_var = ctk.StringVar(value="Gemini 2.5 Flash")
        self.model_dropdown = ctk.CTkOptionMenu(self.model_frame, variable=self.model_var, 
                                                values=["Gemini 2.5 Flash", "Claude 3.5 Sonnet", "Qwen VL Max"])
        self.model_dropdown.pack(side="right", fill="x", expand=True, padx=(10, 0))
        
        # API Key
        self.api_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.api_frame.pack(fill="x", padx=20, pady=5)
        self.api_label = ctk.CTkLabel(self.api_frame, text="API Key:")
        self.api_label.pack(side="left")
        self.api_entry = ctk.CTkEntry(self.api_frame, show="*", placeholder_text="sk-...")
        self.api_entry.pack(side="right", fill="x", expand=True, padx=(10, 0))
        
        # Try load from .env if present
        try:
            from dotenv import load_dotenv
            load_dotenv()
            if os.getenv("GEMINI_API_KEY"):
                self.api_entry.insert(0, os.getenv("GEMINI_API_KEY"))
        except:
            pass

        # Dangerous-mode notice label (hidden by default)
        self.danger_notice = ctk.CTkLabel(
            self,
            text="⚠️  Dangerous mode: AI check is BYPASSED. All prompts auto-accepted.",
            text_color="#FF6B6B",
            wraplength=400,
            font=ctk.CTkFont(size=11)
        )
        # Not packed yet — shown only in Dangerous mode

        # Interval
        self.interval_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.interval_frame.pack(fill="x", padx=20, pady=5)
        self.interval_label = ctk.CTkLabel(self.interval_frame, text="Local Scan Interval (sec):")
        self.interval_label.pack(side="left")
        self.interval_entry = ctk.CTkEntry(self.interval_frame, placeholder_text="2")
        self.interval_entry.insert(0, "2")
        self.interval_entry.pack(side="right", fill="x", expand=True, padx=(10, 0))
        
        # Buttons
        self.button_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.button_frame.pack(fill="x", padx=20, pady=20)
        self.start_btn = ctk.CTkButton(self.button_frame, text="Start", fg_color="green", hover_color="darkgreen", command=self.start_monitoring)
        self.start_btn.pack(side="left", expand=True, padx=5)
        self.stop_btn = ctk.CTkButton(self.button_frame, text="Stop", fg_color="red", hover_color="darkred", state="disabled", command=self.stop_monitoring)
        self.stop_btn.pack(side="right", expand=True, padx=5)

        # Status
        self.log_textbox = ctk.CTkTextbox(self, height=120)
        self.log_textbox.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.log_textbox.insert("end", "Welcome to AIcceptor.\nReady to start.\n")
        self.log_textbox.configure(state="disabled")

    def _on_regime_change(self, value):
        """Show/hide API key section based on selected regime."""
        if value == "Dangerous":
            self.api_frame.pack_forget()
            self.danger_notice.pack(before=self.interval_frame, padx=20, pady=(0, 4))
        else:
            self.danger_notice.pack_forget()
            self.api_frame.pack(before=self.interval_frame, fill="x", padx=20, pady=5)

    def log(self, message):
        def _append():
            self.log_textbox.configure(state="normal")
            self.log_textbox.insert("end", f"> {message}\n")
            self.log_textbox.see("end")
            self.log_textbox.configure(state="disabled")
        self.after(0, _append)

    def start_monitoring(self):
        regime = self.regime_var.get()  # "Safe" | "Dangerous"
        api_key = self.api_entry.get().strip()

        if regime == "Safe" and not api_key:
            self.log("Error: API Key is required in Safe mode.")
            return
            
        try:
            interval = int(self.interval_entry.get().strip())
        except ValueError:
            self.log("Error: Interval must be an integer.")
            return

        self.running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.regime_btn.configure(state="disabled")
        self.model_dropdown.configure(state="disabled")
        self.api_entry.configure(state="disabled")
        self.interval_entry.configure(state="disabled")
        
        mode_label = "SAFE mode (AI analysis ON)" if regime == "Safe" else "DANGEROUS mode (AI analysis OFF)"
        self.log(f"Starting monitoring — {mode_label}")
        self.monitor_thread = threading.Thread(
            target=self.run_loop,
            args=(self.model_var.get(), api_key, interval, regime),
            daemon=True
        )
        self.monitor_thread.start()

    def stop_monitoring(self):
        self.running = False
        self.log("Stopping... please wait for current cycle to finish.")

    def run_loop(self, model_name, api_key, interval, regime="Safe"):
        waiting_for_target = None
        tracked_false_positives = []
        consecutive_api_errors = 0
        
        while self.running:
            self.log(f"Scanning screen locally...")
            screenshot_path = take_screenshot()
            
            ocr_detected, found_buttons = check_local_ocr(screenshot_path)
            
            # 1. Update waiting_for_target
            if waiting_for_target:
                still_present = any(abs(b['x'] - waiting_for_target[0]) < 30 and abs(b['y'] - waiting_for_target[1]) < 30 for b in found_buttons)
                if still_present:
                    self.log("Waiting for prompt to be clicked or manually dismissed...")
                    # Clean up and sleep
                    if os.path.exists(screenshot_path):
                        os.remove(screenshot_path)
                    for _ in range(interval):
                        if not self.running: break
                        time.sleep(1)
                    continue
                else:
                    self.log("Target cleared. Resuming monitoring.")
                    waiting_for_target = None
            
            # 2. Update tracked_false_positives
            new_tracked = []
            for fp in tracked_false_positives:
                if any(abs(b['x'] - fp[0]) < 30 and abs(b['y'] - fp[1]) < 30 for b in found_buttons):
                    new_tracked.append(fp)
            tracked_false_positives = new_tracked
            
            # 3. Filter found_buttons to ignore false positives
            valid_buttons = []
            for b in found_buttons:
                if not any(abs(b['x'] - fp[0]) < 30 and abs(b['y'] - fp[1]) < 30 for fp in tracked_false_positives):
                    valid_buttons.append(b)
            
            if not valid_buttons:
                # Clean up
                if os.path.exists(screenshot_path):
                    os.remove(screenshot_path)
                # Sleep in small chunks so we can interrupt quickly if user clicks "Stop"
                for _ in range(interval):
                    if not self.running: break
                    time.sleep(1)
                continue

            # ── DANGEROUS MODE: skip AI entirely ──────────────────────────────
            if regime == "Dangerous":
                # Prefer "Accept all" button, otherwise take the lowest on screen
                sorted_buttons = sorted(valid_buttons, key=lambda b: b["y"], reverse=True)
                target_btn = next((b for b in sorted_buttons if "all" in b["text"]), sorted_buttons[0])
                x, y = target_btn["x"], target_btn["y"]
                self.log(f"[DANGEROUS] Auto-clicking '{target_btn['text']}' at ({x:.1f}, {y:.1f}) — no AI check.")
                original_x, original_y = pyautogui.position()
                pyautogui.moveTo(x, y, duration=0.2)
                pyautogui.mouseDown()
                time.sleep(0.05)
                pyautogui.mouseUp()
                pyautogui.moveTo(original_x, original_y, duration=0.1)
                waiting_for_target = (x, y)
                if os.path.exists(screenshot_path):
                    os.remove(screenshot_path)
                for _ in range(interval):
                    if not self.running: break
                    time.sleep(1)
                continue
            # ─────────────────────────────────────────────────────────────────

            self.log(f"Prompt detected! Analyzing with {model_name}...")
            
            try:
                if model_name == "Gemini 2.5 Flash":
                    text = call_gemini(screenshot_path, api_key)
                elif model_name == "Claude 3.5 Sonnet":
                    text = call_claude(screenshot_path, api_key)
                elif model_name == "Qwen VL Max":
                    text = call_qwen(screenshot_path, api_key)
                else:
                    raise Exception("Unknown model selected.")
                
                # Success! Reset API error tracking.
                if consecutive_api_errors > 0:
                    self.log("API connection re-established.")
                    consecutive_api_errors = 0
                
                # Clean up JSON
                if text.startswith("```json"):
                    text = text[7:]
                elif text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                    
                result = json.loads(text.strip())
                status = result.get("status")
                
                if status == "SAFE":
                    target_btn = None
                    gemini_coords = result.get("button_coordinates")
                    
                    if gemini_coords and gemini_coords.get("x") is not None and gemini_coords.get("y") is not None and valid_buttons:
                        gx = gemini_coords["x"]
                        gy = gemini_coords["y"]
                        # Sensor Fusion: Snap hallucinated Gemini semantic coords to nearest physical OCR bounding box
                        best_dist = float('inf')
                        for b in valid_buttons:
                            dist = math.hypot(b["x"] - gx, b["y"] - gy)
                            if dist < best_dist:
                                best_dist = dist
                                target_btn = b
                        if target_btn:
                            self.log(f"Sensor Fusion: Snapped Gemini ({gx}, {gy}) to OCR '{target_btn['text']}' at ({target_btn['x']:.1f}, {target_btn['y']:.1f})")
                            
                    # Fallback if Gemini failed to provide coordinates
                    if not target_btn:
                        # Sort buttons by Y coordinate descending (highest Y = lowest on screen).
                        sorted_buttons = sorted(valid_buttons, key=lambda b: b["y"], reverse=True)
                        
                        # Prioritize "Accept All" if present
                        for btn in sorted_buttons:
                            if "all" in btn["text"]:
                                target_btn = btn
                                break
                        if not target_btn and sorted_buttons:
                            target_btn = sorted_buttons[0]
                        
                    if target_btn:
                        x = target_btn["x"]
                        y = target_btn["y"]
                        self.log(f"SAFE detected. OCR Click at ({x:.1f}, {y:.1f}) for '{target_btn['text']}'.")
                        
                        # Temporarily hijack mouse
                        original_x, original_y = pyautogui.position()
                        pyautogui.moveTo(x, y, duration=0.2)
                        pyautogui.mouseDown()
                        time.sleep(0.05)
                        pyautogui.mouseUp()
                        pyautogui.moveTo(original_x, original_y, duration=0.1)
                        
                        waiting_for_target = (x, y)
                    else:
                        self.log("SAFE action, but local OCR lost button coordinates.")
                
                elif status == "UNSAFE":
                    reason = result.get("reason", "Unknown Reason")
                    self.log(f"UNSAFE ACTION DETECTED.")
                    notify_user(message=f"Review needed: {reason}", title="⚠️ AIcceptor Alert")
                    
                    if valid_buttons:
                        lowest_btn = sorted(valid_buttons, key=lambda b: b["y"], reverse=True)[0]
                        waiting_for_target = (lowest_btn["x"], lowest_btn["y"])
                
                elif status == "NONE":
                    self.log("No Antigravity prompt detected. Blacklisting false positive texts.")
                    for b in valid_buttons:
                        tracked_false_positives.append((b["x"], b["y"]))
                    
            except Exception as e:
                consecutive_api_errors += 1
                backoff_time = min(60, (2 ** consecutive_api_errors)) * 10
                self.log(f"API Error. Backing off for {backoff_time} seconds to protect quota...")
                
                # Active sleep backoff mask
                for _ in range(backoff_time):
                    if not self.running: break
                    time.sleep(1)
                
            
            # Clean up
            if os.path.exists(screenshot_path):
                os.remove(screenshot_path)
            
            # Sleep in small chunks so we can interrupt quickly if user clicks "Stop"
            for _ in range(interval):
                if not self.running:
                    break
                time.sleep(1)
        def _reset_gui():
            self.log("Stopped monitoring.")
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self.regime_btn.configure(state="normal")
            self.model_dropdown.configure(state="normal")
            self.api_entry.configure(state="normal")
            self.interval_entry.configure(state="normal")
        
        self.after(0, _reset_gui)

if __name__ == "__main__":
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    
    app = AIcceptorApp()
    app.mainloop()
