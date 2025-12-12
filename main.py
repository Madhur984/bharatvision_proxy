# main.py (async Playwright version)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import traceback
import time
import asyncio

STREAMLIT_URL = "https://bharatvision.streamlit.app"  # <- keep your URL

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

INPUT_SELECTORS = [
    "textarea",
    "div[data-testid='stTextArea'] textarea",
    "div[data-testid='stTextInput'] input",
    "input[type='text']",
    "div[contenteditable='true']",
]

BUTTON_SELECTORS = [
    "button[data-testid='stButton']",
    "button:has-text('Analyze')",
    "button:has-text('Submit')",
    "button:has-text('Run')",
    "button",
]

OUTPUT_SELECTORS = [
    "div.stMarkdown",
    "div[data-testid='stMarkdown']",
    "div[class*='stText']",
    "pre",
    "div.stAlert",
    "div[data-testid='stExpander']",
    "div[role='region']",
]


async def try_fill_and_click(page, text, debug):
    filled = False
    for sel in INPUT_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el:
                # robustly set value
                try:
                    await page.eval_on_selector(sel, """(el, value) => {
                        if (el.tagName.toLowerCase() === 'textarea' || el.tagName.toLowerCase() === 'input') {
                            el.focus();
                            el.value = value;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                        } else {
                            el.innerText = value;
                            el.dispatchEvent(new InputEvent('input', { bubbles: true }));
                        }
                    }""", text)
                    debug.append(f"filled using selector: {sel}")
                    filled = True
                    break
                except Exception as e:
                    debug.append(f"eval fill error {sel}: {repr(e)}")
        except Exception as e:
            debug.append(f"query error {sel}: {repr(e)}")

    if not filled:
        # inject fallback textarea
        try:
            await page.evaluate("""(value) => {
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

    clicked = False
    for sel in BUTTON_SELECTORS:
        try:
            btn = await page.query_selector(sel)
            if btn:
                try:
                    await btn.scroll_into_view_if_needed()
                    await btn.click(timeout=5000)
                    debug.append(f"clicked button using selector: {sel}")
                    clicked = True
                    break
                except PlaywrightTimeoutError:
                    debug.append(f"timeout clicking {sel}")
                except Exception as e:
                    debug.append(f"error clicking {sel}: {repr(e)}")
        except Exception as e:
            debug.append(f"button query error {sel}: {repr(e)}")

    if (not clicked):
        # try pressing Enter on injected textarea
        try:
            injected = await page.query_selector("#__bv_injected_textarea")
            if injected:
                await page.focus("#__bv_injected_textarea")
                await page.keyboard.press("Enter")
                debug.append("pressed Enter in injected textarea")
                clicked = True
        except Exception as e:
            debug.append(f"error pressing Enter: {repr(e)}")

    return filled, clicked


async def extract_outputs(page, debug):
    texts = []
    for sel in OUTPUT_SELECTORS:
        try:
            nodes = await page.query_selector_all(sel)
            for n in nodes:
                try:
                    t = (await n.inner_text()).strip()
                    if t and t not in texts:
                        texts.append(t)
                        debug.append(f"extracted using {sel}")
                except Exception as e:
                    debug.append(f"error reading inner_text for {sel}: {repr(e)}")
        except Exception as e:
            debug.append(f"query error for {sel}: {repr(e)}")

    if not texts:
        try:
            body = (await page.inner_text("body"))[:20000]
            debug.append("extracted body fallback")
            texts.append(body)
        except Exception as e:
            debug.append(f"body extraction failed: {repr(e)}")
    return "\n\n---\n\n".join(texts)


async def run_streamlit_async(text: str, timeout_s: int = 45):
    debug = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            page.set_default_timeout(timeout_s * 1000)

            debug.append(f"navigating to {STREAMLIT_URL}")
            await page.goto(STREAMLIT_URL, wait_until="networkidle")
            await asyncio.sleep(1.2)
            debug.append("page loaded")

            filled, clicked = await try_fill_and_click(page, text, debug)

            tstart = time.time()
            outputs = ""
            while time.time() - tstart < timeout_s:
                outputs = await extract_outputs(page, debug)
                if outputs and len(outputs) > 10:
                    debug.append("sufficient output found, breaking wait loop")
                    break
                await asyncio.sleep(1.0)

            try:
                snapshot = (await page.content())[:200000]
            except Exception as e:
                snapshot = f"snapshot_error:{repr(e)}"

            await browser.close()
            return {"result": outputs or "", "debug": debug, "filled": filled, "clicked": clicked, "snapshot_snippet": snapshot[:5000]}
    except Exception as e:
        return {"error": "proxy_exception", "trace": traceback.format_exc(), "debug": debug}


@app.post("/validate")
async def validate(payload: dict):
    text = payload.get("text", "")
    if not text:
        return {"error": "No text provided"}
    # Call the async function
    return await run_streamlit_async(text)
