# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import traceback
import time

STREAMLIT_URL = "https://bharatvision.streamlit.app"  # <- already provided

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Candidate selectors / strategies
INPUT_SELECTORS = [
    "textarea",                                 # generic
    "div[data-testid='stTextArea'] textarea",   # streamlit textarea
    "div[data-testid='stTextInput'] input",      # text input
    "input[type='text']",
    "div[contenteditable='true']",               # sometimes editors
]

BUTTON_SELECTORS = [
    "button[data-testid='stButton']",
    "button:has-text('Analyze')",
    "button:has-text('Submit')",
    "button:has-text('Run')",
    "button",  # fallback to first button
]

OUTPUT_SELECTORS = [
    "div.stMarkdown",                # common Streamlit markdown output
    "div[data-testid='stMarkdown']",
    "div[class*='stText']",
    "pre",                           # preformatted
    "div.stAlert",
    "div[data-testid='stExpander']",
    "div[role='region']",
]

def try_fill_and_click(page, text, debug):
    """Try many strategies to fill input and click a submit button."""
    # 1) Try to fill a textarea/input
    filled = False
    for sel in INPUT_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el:
                # Use evaluate to set value and dispatch input events (robust)
                page.eval_on_selector(sel, """(el, value) => {
                    if (el.tagName.toLowerCase() === 'textarea' || el.tagName.toLowerCase() === 'input') {
                        el.focus();
                        el.value = value;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                    } else {
                        // contenteditable
                        el.innerText = value;
                        el.dispatchEvent(new InputEvent('input', { bubbles: true }));
                    }
                }""", text)
                debug.append(f"filled using selector: {sel}")
                filled = True
                break
        except Exception as e:
            debug.append(f"error filling {sel}: {repr(e)}")
    if not filled:
        # as last resort inject a textarea into page and fill it
        try:
            page.evaluate("""(value) => {
                let ta = document.createElement('textarea');
                ta.style.position = 'fixed';
                ta.style.left = '10px';
                ta.style.top = '10px';
                ta.id = '__bv_injected_textarea';
                document.body.appendChild(ta);
                ta.value = value;
            }""", text)
            debug.append("injected fallback textarea (#__bv_injected_textarea)")
            filled = True
        except Exception as e:
            debug.append(f"failed to inject textarea: {repr(e)}")

    # 2) Try clicking button using multiple strategies
    clicked = False
    for sel in BUTTON_SELECTORS:
        try:
            # prefer visible buttons
            btn = page.query_selector(sel)
            if btn:
                btn.scroll_into_view_if_needed()
                btn.click(timeout=5000)
                debug.append(f"clicked button using selector: {sel}")
                clicked = True
                break
        except PlaywrightTimeoutError:
            debug.append(f"timeout clicking {sel}")
        except Exception as e:
            debug.append(f"error clicking {sel}: {repr(e)}")

    # 3) If nothing clicked and we injected textarea, try pressing Enter in that textarea
    if (not clicked) and page.query_selector("#__bv_injected_textarea"):
        try:
            page.focus("#__bv_injected_textarea")
            page.keyboard.press("Enter")
            debug.append("pressed Enter in injected textarea")
            clicked = True
        except Exception as e:
            debug.append(f"error pressing Enter: {repr(e)}")

    return filled, clicked

def extract_outputs(page, debug):
    """Try multiple selectors and return concatenated text of matches (deduped)."""
    texts = []
    for sel in OUTPUT_SELECTORS:
        try:
            nodes = page.query_selector_all(sel)
            for n in nodes:
                try:
                    t = n.inner_text().strip()
                    if t and t not in texts:
                        texts.append(t)
                        debug.append(f"extracted using {sel}")
                except Exception as e:
                    debug.append(f"error reading inner_text for {sel}: {repr(e)}")
        except Exception as e:
            debug.append(f"query error for {sel}: {repr(e)}")

    # Last resort: grab entire body text snapshot
    if not texts:
        try:
            body = page.inner_text("body")[:20000]
            debug.append("extracted body fallback")
            texts.append(body)
        except Exception as e:
            debug.append(f"body extraction failed: {repr(e)}")

    return "\n\n---\n\n".join(texts)

def run_streamlit(text: str, timeout_s: int = 45):
    debug = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            page.set_default_timeout(timeout_s * 1000)

            debug.append(f"navigating to {STREAMLIT_URL}")
            page.goto(STREAMLIT_URL, wait_until="networkidle")
            time.sleep(1.2)
            debug.append("page loaded")

            filled, clicked = try_fill_and_click(page, text, debug)

            # Wait for changes to appear; poll outputs
            tstart = time.time()
            outputs = ""
            while time.time() - tstart < timeout_s:
                outputs = extract_outputs(page, debug)
                if outputs and len(outputs) > 10:
                    debug.append("sufficient output found, breaking wait loop")
                    break
                time.sleep(1.0)

            # snapshot for debug (optional small HTML capture)
            try:
                snapshot = page.content()[:200000]  # keep small
            except Exception as e:
                snapshot = f"snapshot_error:{repr(e)}"

            browser.close()
            return {"result": outputs or "", "debug": debug, "filled": filled, "clicked": clicked, "snapshot_snippet": snapshot[:5000]}
    except Exception as e:
        return {"error": "proxy_exception", "trace": traceback.format_exc(), "debug": debug}

@app.post("/validate")
async def validate(payload: dict):
    text = payload.get("text", "")
    if not text:
        return {"error": "No text provided"}
    return run_streamlit(text)
