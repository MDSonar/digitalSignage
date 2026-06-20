# Litmus Signage Dashboard & Web Player - Feature Documentation

## Version: 2.2.0

### Overview
A Flask-based digital signage system with a **visual timeline scheduler**, **zoom controls**, **timezone management**, **multiple time slots per item**, and an **auto-hide player UI**.

---

## 📊 Dashboard Features

### 1. **Global Timezone Bar** (Always Visible)
- **Location**: Below header, permanent strip
- **Components**:
  - Current server time (HH:MM format)
  - Timezone display badge
  - Timezone selector dropdown (20+ options)
  - Custom timezone input
  - Apply button with feedback status
- **Behavior**:
  - Shows current time in configured timezone
  - Allows switching between Asia/Kolkata, Europe/*, America/*, UTC, etc.
  - Mismatch warning: if browser time differs >2 min from server time
  - Settings persisted in `signage_config.json`

---

### 2. **Visual Timeline Scheduler**

#### 2a. **Zoom & Pan Controls**
- **7 Zoom Levels**: 24h → 12h → 6h → 4h → 2h → 1h → 30m
- **Controls**:
  - Zoom slider (−/+)
  - Pan buttons (◀/▶) — enabled only when view is zoomed
  - "Now" button — jump to current time
  - Zoom label showing current view duration
- **Features**:
  - Maintains view center when zooming
  - Pan clamped to valid 24-hour range
  - Minimap shows full day with view window indicator

#### 2b. **Multiple Time Slots Per Item**
- **One lane per unique item name** (not per playlist entry)
- **"+" button on lane label** → Add another time slot for the same item
- **Smart slot placement**:
  - Tries to place new slot at current time (green line)
  - Falls back to first free gap after existing blocks
  - Auto-pans view to show the new slot
- **Slot size** scales with zoom (proportional to view duration)
- **Remove blocks**: Click × on individual time slot (item stays in playlist)

#### 2c. **Drag & Resize Blocks**
- **Move**: Drag center of block (whole 24h moves with it)
- **Resize**: Drag left/right edge independently
- **Real-time update**: Changes visible immediately
- **Green glow**: Highlights blocks active right now

#### 2d. **Daily vs One-Time Schedules**
- **Tab toggle**: Switch between recurring (Daily) and one-off (One-Time)
- **One-Time**: Date picker shown; only blocks matching that date visible
- **Schedule metadata**: Displayed as colored badges below item name
- **Multiple badges**: Each item can show all its scheduled slots

---

### 3. **Scheduler Priority & Overlap Handling**

#### When Multiple Items Overlap:
1. **If scheduler_default is set** → Default plays (ignores other active items)
2. **If no default set** → First item in playlist sequence plays
3. **Multiple slots of same item** → All contribute to that item's schedule check

#### In Playlist Editor:
- Shows all schedule slots as inline badges (colored by item name, not index)
- Items without schedule show hint: "No schedule — drag on timeline to add"

---

### 4. **Repeat Function Disabled When Scheduler Active**
- **Behavior**:
  - When scheduler OFF: Repeat input enabled, full opacity
  - When scheduler ON: Repeat input disabled, greyed out (35% opacity), cursor: not-allowed
  - Tooltip: "Repeat is ignored when the scheduler is enabled"
- **Reason**: Scheduler controls playback sequence; repeat becomes redundant
- **Re-renders** automatically when scheduler toggle flipped

---

### 5. **Playlist Editor Improvements**
- **Item layout**: Each item is one draggable row
- **Actions**: Drag to reorder, set repeat count (when scheduler off), view all schedule slots
- **Save flow**: Changes saved to selected playlist on Save button click
- **Color mapping**: Items with same name get same color across timeline and badges

---

### 6. **Backward Compatibility**
- **Old schedule format** (single object): Automatically converted to array on load
- **Legacy playlists**: Continue to work with `repeat` field
- **Migration**: `normalizePlaylistItems()` handles both formats transparently

---

## 🎬 Web Player Features

### 1. **Persistent Player Controls** (Auto-Hide)
- **Position**: Centered at bottom, always-on-top (z-index: 1000)
- **Components**:
  - ← Back button (return to playlist selection)
  - ⛶ Fullscreen button (toggle)
- **Behavior**:
  - Hidden when pointer not moving
  - Appear on any pointer move
  - Auto-hide after **5 seconds** of inactivity
  - Smooth fade in/out (opacity transition 0.3s)

### 2. **Cursor Auto-Hide**
- **Default state**: Visible cursor
- **After 5 sec no movement**: `cursor: none`
- **Triggered by**: `mousemove`, `mousedown`, `touchstart`
- **Re-trigger**: Any pointer activity resets timer
- **CSS class**: `body.playing.cursor-hidden` when hidden

### 3. **Fullscreen Toggle**
- **First playback**: Auto-enters fullscreen (on `startPlayback()`)
- **After Escape**: User returns to windowed mode
- **Button icon**: Swaps between enter (⛶ expand) and exit (⛶ compress) icons
- **Listener**: `fullscreenchange` event updates icon automatically
- **Click to toggle**: Always available when pointer visible

### 4. **Removed Badge**
- Scheduler badge ("📅 Scheduled") completely removed
- No visual indicator needed; scheduler works silently in background

---

## 🔌 Backend Architecture

### 1. **Client-Side Schedule Evaluation**
- **Web Player**: Evaluates `raw_items` schedule array using device's local time (`new Date()`)
- **Never server-side filtering**: Avoids timezone mismatch between Docker UTC and display device local time
- **API returns**:
  - `playlist`: Expanded items (videos/slides)
  - `raw_items`: Original items with schedule metadata
  - `scheduler_enabled`: Boolean flag
  - `scheduler_default`: Fallback item if no match

### 2. **Priority Logic** (Web Player `evalSchedule()`)
```javascript
// Check all items' schedule arrays for matches
if (multiple items active) {
  if (scheduler_default set && default is active) {
    // Play ONLY the default
  } else {
    // Play first item in sequence order
  }
}
if (nothing active) {
  // Play scheduler_default (if set), else all items
}
```

### 3. **Data Structure**
```json
{
  "name": "Item Name",
  "repeats": 1,
  "schedule": [
    {
      "recurrence": "daily" | "once",
      "start_time": "HH:MM",
      "end_time": "HH:MM",
      "date": "YYYY-MM-DD"  // only for "once"
    }
  ]
}
```

### 4. **Config Management**
- **File**: `~/signage/signage_config.json`
- **Contents**: `{ "timezone": "Asia/Kolkata" }`
- **Priority**: Config file > TZ env var > system local time
- **API**: `GET/POST /api/config` for dashboard UI

---

## 🛠 Technical Details

### Dashboard Timeline (`renderTimeline()`)
- Renders one lane per unique item name (deduplicates by name)
- Multiple schedule blocks per lane (array iteration)
- Color map built once per render (`buildNameColorMap()`)
- Ruler adapts tick/label spacing based on zoom level

### Dashboard Drag Handler
- Uses `tlDragState = { idx, schedIdx, name, type, ... }`
- `schedIdx` targets the specific schedule entry in the array
- Preserves other schedule slots when editing one

### Web Player Schedule Check (`evalSchedule()`)
- Supports both legacy single-object and new array format
- Iterates schedule array for each item
- Breaks on first window match (one match per item enough)
- Applies priority when multiple items match

---

## 📋 State Variables (Dashboard)

| Variable | Type | Purpose |
|----------|------|---------|
| `currentPlaylistItems` | Array | Loaded playlist items with schedule arrays |
| `currentSchedulerEnabled` | Boolean | Scheduler toggle state |
| `currentSchedulerDefault` | Object | Fallback item when nothing scheduled |
| `tlCurrentView` | String | 'daily' or 'once' |
| `tlOnceDate` | String | YYYY-MM-DD for one-time schedule |
| `tlZoomStep` | Number | 0–6 zoom level index |
| `tlViewStart` | Number | Start minute of visible window |
| `tlDragState` | Object | In-progress block drag metadata |
| `nameColorMap` | Object | Name → color assignments |

---

## 📋 State Variables (Web Player)

| Variable | Type | Purpose |
|----------|------|---------|
| `playlist` | Array | Current playback items |
| `currentIndex` | Number | Position in playlist |
| `scheduledKey` | String | Change-detection: hash + item list |
| `activityTimer` | ID | Timeout for cursor/controls hide |
| `isPlaying` | Boolean | Media playback active |
| `youtubePlayer` | Object | YouTube iframe API object |

---

## 🎨 Color Scheme

- **Item colors** (by name, not index): 10-color palette repeating
  - Indigo, Amber, Teal, Red, Purple, Cyan, Orange, Pink, Emerald, Violet
- **Timeline block active** (now playing): Green glow + `tl-block-active` class
- **Controls**: Dark glass morphism (rgba blur + backdrop-filter)

---

## 🔐 Security Notes

- **Timezone input**: Validated server-side with `ZoneInfo()` before saving
- **Schedule times**: Hardcoded format (HH:MM), no user-provided format strings
- **Playlist names**: HTML-escaped on display
- **Media URLs**: Served from controlled `/content` routes

---

## 📱 Responsive Design

- **Dashboard**: Max-width 900px, centered, single-column
- **Timeline**: Adapts to available width; horizontal scroll on narrow screens
- **Web Player**: Fullscreen or windowed, 100vw/100vh
- **Controls**: Scale-agnostic (fixed button sizes, absolute positioning)

---

## 🚀 Deployment Notes

1. **Restart containers** after code changes: `docker-compose down && docker-compose up -d`
2. **Config persistence**: `~/signage/signage_config.json` survives container restarts
3. **Timezone in Docker**: Set via dashboard (saved to config), or `TZ` env var as fallback
4. **Browser cache**: Web player has `Cache-Control: no-cache` to force playlist refresh

---

## 🐛 Known Limitations

1. **Overlapping blocks**: Stacked visually (z-index handled), no collision detection UI
2. **Midnight crossover**: Schedules wrapping 23:00–01:00 not specially handled (appears as gap)
3. **Fullscreen Escape**: Cannot re-enter fullscreen on same click on some browsers (must move pointer)
4. **YouTube duration**: Uses placeholder 30s default (no ffprobe integration)

---

## 🎯 Testing Checklist

- [ ] Add item, click + to create 2nd slot → slot appears at current time
- [ ] Drag slot left/right → time updates, block moves
- [ ] Zoom in/out → view centers, pan buttons toggle
- [ ] Click "Now" → jump to current time
- [ ] Switch timezone → time updates, web player re-evaluates
- [ ] Schedule two items overlapping → higher one in sequence plays (or default if set)
- [ ] Move pointer over player → controls fade in
- [ ] Wait 5 sec with no movement → cursor hides, controls fade out
- [ ] Click fullscreen button → enters fullscreen, icon changes
- [ ] Press Escape → exits fullscreen, icon changes, button still works
- [ ] Disable scheduler → repeat input becomes enabled
- [ ] Enable scheduler → repeat input becomes disabled

---

**Last Updated**: 2026-06-20  
**Status**: Production Ready ✅
