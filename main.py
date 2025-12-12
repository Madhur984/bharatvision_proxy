# main.py - diagnostic async proxy (Playwright)
import os, time, traceback, asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

LOGIN_URL = "https://bharatvision.streamlit.app"
APP_URL = "https://bharatvision.streamlit.app"
BV_USER = os.getenv("BV_USER")
BV_PASS = os.getenv("BV_PASS")
HEADLESS = os.getenv("HEADLESS", "true").lower() != "false"

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# selectors
USERNAME_SEL = 'input[placeholder="Enter username"]'
PASSWORD_SEL = 'input[placeholder="Enter password"]'
LOGIN_BTN_SEL = 'button:has-text("Login")'

INPUT_CANDIDATES = [
    "textarea",
    "div[data-testid='stTextArea'] textarea",
    "div[data-testid='stTextInput'] input",
    "input[type='text']",
    "div[contenteditable='true']",
    "input[placeholder='Enter text']",
]

OUTPUT_CANDIDATES = [
    "div.stMarkdown",
    "div[data-testid='stMarkdown']",
    "pre",
    "div[class*='stText']",
]

async def do_login_if_needed(page, debug):
    """Try to login if BV_USER/BV_PASS are set and if login form is present."""
    try:
        await page.goto(LOGIN_URL, wait_until="networkidle")
        await asyncio.sleep(1.5)
        debug.append(f"loaded login url: {page.url}")
        # if login fields exist, attempt login
        user_el = await page.query_selector(USERNAME_SEL)
        pass_el = await page.query_selector(PASSWORD_SEL)
        if user_el and pass_el and BV_USER and BV_PASS:
            debug.append("login form detected - attempting credentials fill")
            try:
                await page.fill(USERNAME_SEL, BV_USER)
                debug.append("filled username")
            except Exception as e:
                debug.append(f"username fill error: {repr(e)}")
            try:
                await page.fill(PASSWORD_SEL, BV_PASS)
                debug.append("filled password")
            except Exception as e:
                debug.append(f"password fill error: {repr(e)}")
            try:
                await page.click(LOGIN_BTN_SEL)
                debug.append("clicked login button")
            except Exception as e:
                debug.append(f"login click error: {repr(e)}")
            # wait after login
            await asyncio.sleep(3.5)
            debug.append(f"url after login attempt: {page.url}")
            return
        else:
            debug.append("no login form detected or missing BV_USER/BV_PASS - skipping login step")
            return
    except Exception:
        debug.append("exception during login attempt: " + traceback.format_exc())
        return

async def find_input_and_click(page, text, debug):
    filled = False
    clicked = False
    # Try many input selectors
    for sel in INPUT_CANDIDATES:
        try:
            el = await page.query_selector(sel)
            if el:
                try:
                    await el.fill(text)
                    debug.append(f"filled input using selector: {sel}")
                    filled = True
                    break
                except Exception as e:
                    debug.append(f"fill error for {sel}: {repr(e)}")
        except Exception as e:
            debug.append(f"query error for input sel {sel}: {repr(e)}")
    # Try clicking a stButton or first button
    try:
        btn = await page.query_selector('button[data-testid="stButton"]')
        if btn:
            await btn.click()
            debug.append("clicked stButton")
            clicked = True
    except Exception as e:
        debug.append(f"stButton click error: {repr(e)}")
    if not clicked:
        try:
            bb = await page.query_selector("button")
            if bb:
                await bb.click()
                debug.append("clicked first <button>")
                clicked = True
        except Exception as e:
            debug.append(f"generic button click error: {repr(e)}")
    return filled, clicked

async def extract_output(page, debug):
    texts = []
    for sel in OUTPUT_CANDIDATES:
        try:
            nodes = await page.query_selector_all(sel)
            for n in nodes:
                try:
                    t = (await n.inner_text()).strip()
                    if t and t not in texts:
                        texts.append(t)
                        debug.append(f"extracted via {sel}")
                except Exception as e:
                    debug.append(f"inner_text error {sel}: {repr(e)}")
        except Exception as e:
            debug.append(f"query_all error {sel}: {repr(e)}")
    # fallback to body snapshot
    if not texts:
        try:
            body = await page.inner_text("body")
            texts.append(body[:80000])
            debug.append("body fallback extracted")
        except Exception as e:
            debug.append(f"body fallback error: {repr(e)}")
    return "\n\n---\n\n".join(texts)

async def run_playwright(text, timeout_s=45):
    debug = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])
            page = await browser.new_page()
            page.set_default_timeout(timeout_s * 1000)

            debug.append("navigating to login/app")
            await do_login_if_needed(page, debug)

            # Navigate to app URL explicitly to ensure we are at app page
            try:
                await page.goto(APP_URL, wait_until="networkidle")
                debug.append(f"navigated to APP_URL, current url: {page.url}")
            except Exception as e:
                debug.append(f"goto APP_URL error: {repr(e)}")

            # give JS time to build UI
            await asyncio.sleep(2.5)

            # Try to find input and click
            filled, clicked = await find_input_and_click(page, text, debug)
            debug.append(f"filled={filled}, clicked={clicked}")

            # Wait a bit then attempt to extract
            tstart = time.time()
            output = ""
            while time.time() - tstart < timeout_s:
                output = await extract_output(page, debug)
                if output and len(output) > 10:
                    debug.append("sufficient output found")
                    break
                await asyncio.sleep(1.0)

            try:
                snap = await page.content()
                snapshot = snap[:120000]
            except Exception as e:
                snapshot = f"snapshot_error:{repr(e)}"

            cur_url = page.url
            await browser.close()
            return {"result": output, "debug": debug, "page_url_after_login": cur_url, "snapshot_snippet": snapshot}
    except Exception as e:
        return {"error": "proxy_exception", "trace": traceback.format_exc(), "debug": debug}

@app.post("/validate")
async def validate(payload: dict):
    text = payload.get("text", "")
    if not text:
        return {"error": "No text provided"}
    return await run_playwright(text)
