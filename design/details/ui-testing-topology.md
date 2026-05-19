# UI Testing Topology — Natural Language Test Cases

**Status:** Design note — future reference topology  
**Design ref:** §4.2 (reference topologies), §6.2 (skill categories)

## Problem

UI test maintenance is expensive. Selenium/Playwright tests break when
CSS selectors change, IDs get renamed, or layouts shift. Teams spend
60%+ of QA effort maintaining selectors rather than writing new tests.
Business analysts understand the workflows but can't write test code.
Visual regressions slip through functional tests.

## Idea

Replace coded UI tests with natural language prompts. An agent
navigates the UI via browser automation (Playwright MCP), uses a
vision model to understand what's on screen, and validates outcomes
visually — like a human tester would.

```
# Instead of:
selenium.find_element(By.ID, "orderSearch").send_keys("12345")
selenium.find_element(By.CSS, ".btn-search").click()
assert "Order Found" in selenium.page_source

# Write:
"Search for order 12345 and verify the order details page shows
 the correct customer name, order status, and line items."
```

## Why this works for testing (but not production automation)

| Concern | Production automation | UI testing |
|---------|----------------------|------------|
| Reliability bar | 99.9% (wrong click = damage) | 80% useful (wrong click = test fail) |
| Wrong action cost | Real damage | Harmless — just a failed test |
| Finding broken things | Bad | Good — that's the goal |
| Selector dependency | Must be exact | Not needed — vision-based |
| Speed requirement | Real-time | Batch is fine |

## Architecture

```yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: ui-testing
  description: >
    Execute natural language UI test cases via browser automation.
    Takes test prompts, navigates the UI, validates visually, and
    reports pass/fail with screenshot evidence.
agents:
  root:
    id: root
    role: root
    archetype: test-coordinator
    children:
      - id: navigator
        role: worker
        archetype: ui-navigator
      - id: validator
        role: worker
        archetype: visual-validator
```

### Agent responsibilities

**Test Coordinator (root)**
- Parses test suite (list of natural language test cases)
- Routes each test to the navigator
- Collects results and produces a report

**UI Navigator (worker)**
- Controls the browser via Playwright MCP (click, type, navigate, wait)
- Takes screenshots at each step
- Follows natural language instructions mapped to UI actions
- Reads workflow wiki pages for application-specific knowledge

**Visual Validator (worker)**
- Receives screenshots from the navigator
- Uses a vision model (Gemini Flash / Claude) to verify expected state
- Checks: correct page loaded? expected text visible? error messages?
  layout correct? data matches expectations?
- Returns pass/fail with evidence (annotated screenshot + reasoning)

## Skills

| Skill | Category | MCP Server | Description |
|-------|----------|------------|-------------|
| browser-navigate | capability | playwright | Go to URL |
| browser-click | capability | playwright | Click element by description |
| browser-type | capability | playwright | Type text into field |
| browser-screenshot | capability | playwright | Capture current page |
| browser-wait | capability | playwright | Wait for element/condition |
| visual-check | decision | vision model | Verify screenshot matches expectation |
| test-report | persistence | filesystem | Write test results |

## Test case format

Test cases are natural language descriptions in a YAML file:

```yaml
test_suite: Sterling OMS — Order Management
base_url: https://sterling.company.com/oms/
tests:
  - name: Search for existing order
    steps: |
      Navigate to the order search page.
      Enter order number "12345" in the search field.
      Click the search button.
      Verify the order details page loads with status "Shipped".

  - name: Create return for delivered order
    steps: |
      Search for order "67890".
      Click "Create Return" button.
      Select all line items for return.
      Choose return reason "Damaged".
      Submit the return request.
      Verify the return confirmation page shows a return order number.

  - name: Verify inventory dashboard loads
    steps: |
      Navigate to the inventory dashboard.
      Verify the page loads without errors.
      Check that at least one warehouse is listed.
      Take a screenshot for visual regression baseline.
```

## Execution

```bash
# Run a test suite
swarmkit run . ui-testing --input "Run tests from test-cases/order-management.yaml"

# Run a single test interactively
swarmkit chat . ui-testing
> Test that the login page works with valid credentials
> Now test with invalid password — should show error message
```

## Output

```markdown
# Test Results — Sterling OMS Order Management

**Run:** 2026-05-06T10:30:00  
**Passed:** 4/5  
**Failed:** 1/5

## ✅ Search for existing order
- Navigated to order search
- Entered "12345", clicked search
- Order details loaded: status "Shipped" ✓
- Screenshot: screenshots/test-1-result.png

## ❌ Create return for delivered order
- Navigated to order "67890"
- Clicked "Create Return"
- **FAILED:** Return button was disabled. Page shows "Returns not
  available for this order type."
- Screenshot: screenshots/test-2-failure.png
```

## What this solves

1. **No selector maintenance** — vision-based, not CSS-dependent
2. **Business-readable tests** — analysts write prompts, not code
3. **Visual regression detection** — catches layout/style issues
4. **Cross-browser testing** — same prompt, different browser config
5. **Exploratory testing** — "explore the admin panel and report
   anything that looks broken"

## Limitations

- **Slower than coded tests** — each step needs screenshot + model call
  (~3-5 seconds per action vs milliseconds for Selenium)
- **Non-deterministic** — same test may take slightly different paths
- **Complex enterprise UIs** — dense layouts with many similar elements
  may confuse the vision model
- **Authentication** — handling SSO, MFA, session management
- **Not a replacement for unit/API tests** — this is for UI-level
  validation only

## Prerequisites

- Playwright MCP server (`npx @anthropic/playwright-mcp` or similar)
- Vision-capable model for validation (Gemini Flash recommended)
- The application under test accessible via browser

## Primary use case: AI-generated Selenium/Playwright tests

The most valuable mode is NOT running vision-based tests repeatedly —
it's generating coded tests that run for free forever.

### How it works

The agent navigates the UI once using vision + Playwright MCP. While
navigating, it captures two things simultaneously:

1. **Vision model** identifies WHAT to interact with ("the order search
   field", "the submit button", "the status dropdown")
2. **Playwright DOM access** extracts HOW to select it (CSS selector,
   ID, XPath, aria-label, data-testid)

The agent then generates Selenium/Playwright code:

```python
# Generated from: "Search for order 12345 and verify it shows Shipped"

def test_search_order():
    driver.get("https://sterling.company.com/oms/orders")

    # Vision identified: "Order Search" input field
    # DOM extracted: id="orderSearchInput", placeholder="Enter order number"
    search_field = driver.find_element(By.ID, "orderSearchInput")
    search_field.send_keys("12345")

    # Vision identified: blue "Search" button next to the input
    # DOM extracted: button.btn-primary[data-action="search"]
    search_btn = driver.find_element(By.CSS_SELECTOR, "button[data-action='search']")
    search_btn.click()

    # Vision identified: order status showing "Shipped" in green
    # DOM extracted: span.order-status with text content
    WebDriverWait(driver, 10).until(
        EC.text_to_be_present_in_element(
            (By.CSS_SELECTOR, "span.order-status"), "Shipped"
        )
    )
```

### Economics

| Approach | Cost to create | Cost per run | Maintenance |
|----------|---------------|-------------|-------------|
| Manual Selenium | Developer: 4-8 hours | Free | High (selector rot) |
| Vision-based testing | $0.13 per test | $0.13 per run | Low |
| **AI-generated Selenium** | **$0.13 one-time** | **Free** | **Regenerate on UI change** |

When selectors break (UI redesign, ID change, new layout), you don't
fix them manually — re-run the generator against the current UI and it
produces fresh selectors. One $0.13 vision run replaces hours of
developer time hunting for the right CSS path.

### Selector strategy

The generator should produce resilient selectors in priority order:

1. `data-testid` attributes (most stable, if the app uses them)
2. `aria-label` / `role` attributes (accessibility-based, stable)
3. `id` attributes (stable but not always present)
4. Text content selectors (`contains(text(), "Search")`)
5. CSS class selectors (least stable, last resort)

The vision model identifies the element visually. Playwright extracts
ALL available selectors from the DOM. The generator picks the most
resilient one based on the priority above.

### Workflow

```
Natural language test case (written by BA/QA)
    ↓
Agent navigates UI with vision + Playwright (one-time, ~$0.13)
    ↓
Captures: screenshots + DOM selectors at each step
    ↓
Generates: Selenium/Playwright Python test code
    ↓
Human reviews generated code (approve/adjust)
    ↓
Tests run in CI for free on every commit
    ↓
UI changes? Re-run generator ($0.13) → fresh selectors
```

### Additional output modes

**Documentation generation:** Same workflow but outputs a markdown
guide with annotated screenshots instead of test code. Each step
becomes a numbered instruction with a screenshot callout.

**Interactive walkthrough:** Screen recording of the agent's
navigation with generated voiceover from step descriptions. Requires
ffmpeg for recording + TTS for narration.

## Open questions

1. How to handle dynamic data (order numbers, timestamps) that change
   between runs?
2. Should baseline screenshots be stored for visual diff comparison?
3. How to authenticate with SSO-protected enterprise apps?
4. Can test results feed back into a Rynko Flow gate for CI/CD?
5. Should generated tests include page object model (POM) classes or
   flat test functions?
6. How to handle multi-tab/multi-window scenarios?
7. Can the generator produce tests in multiple frameworks (Selenium,
   Playwright, Cypress) from the same navigation run?
