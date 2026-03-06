# Testing Quotes & Playlist Creation

## Step-by-Step Testing Instructions

### 1. **Create a New Playlist**
- Open browser: http://localhost:5000
- Login with: `admin` / `signage`
- Go to **📂 Playlists** section at top
- In the text field labeled "New playlist name", type: `Test Playlist`
- Click the **Create** button
- **IMPORTANT**: Open **DevTools** (F12 or Ctrl+Shift+I) and check the **Console** tab
- Look for messages starting with `[loadQuotes]` or error messages
- **Report what you see in the console**

### 2. **Add a Quote**
- Scroll down to **✨ Inspirational Quotes** section
- In the "Quote Text" textarea, type: `Life is beautiful`
- In the "Author" field, type: `Albert Einstein`
- Click **Add Quote** button
- **IMPORTANT**: Watch the **Console** in DevTools
- Look for: `[addQuote] CALLED` or error messages
- **Report what you see in the console**

### 3. **Check Quotes List**
- After clicking Add Quote, scroll down to see the **Quotes List** section
- You should see your quote appears there with a Delete button
- If you don't see it, check the console for errors

### 4. **Upload Background Image**
- Still in the Quotes section, look for "Upload Background Image" box
- Try clicking on it or dragging an image file onto it
- Watch console for upload status

---

## Common Issues & Solutions

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Buttons don't respond | IIFE scope issue | Already fixed - functions are global |
| `ReferenceError: addQuote is not defined` | Function not in global scope | Check console - should have `[addQuote] CALLED` |
| 401 or 403 error | Session not authenticated | Make sure you logged in and cookies are saved |
| Empty Quotes List | Data not persisting | Check if `/api/quotes` returns data |
| Network error (404) | Wrong endpoint | Check the URL in Network tab |

---

## Browser DevTools Checklist

When testing, **always open DevTools (F12)**:

1. **Console Tab**: Look for `[addQuote]`, `[loadQuotes]`, etc. messages
2. **Network Tab**: Click "Add Quote" and watch for POST request to `/api/quotes`
   - Should return **200** status with `{"ok": true, "id": "..."}`
3. **Elements Tab**: Right-click the Add Quote button, inspect it
   - Should see: `<button onclick="addQuote()">Add Quote</button>`

---

## Quick API Test (if buttons fail)

If buttons don't work, test the API directly:

```bash
# Create playlist
curl -X POST http://localhost:5000/api/playlists \
  -d "name=TestPL" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -b "your_session_cookie"

# Add quote
curl -X POST http://localhost:5000/api/quotes \
  -d "text=Test&author=Me" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -b "your_session_cookie"
```

---

**After trying above steps, please tell me:**
1. What appears in the Console tab?
2. What happens when you click Add Quote?
3. Does the Quotes List update?
4. Any error messages?
