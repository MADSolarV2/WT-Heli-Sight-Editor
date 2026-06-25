# WT Heli Sight Editor

A freeform vector editor for customizing the helicopter rocket/CCIP reticle in War Thunder.

Draw your own reticle using lines and ellipses, pick a color, and export directly to your game — no manual scripting required.

**By MADSolar** — [YouTube: @MADSolarV2](https://www.youtube.com/@MADSolarV2/videos)

---

## What It Does

War Thunder's helicopter rocket sight is drawn entirely in Squirrel script using vector commands. This editor lets you design a custom reticle visually and patches it into the game's GUI files automatically.

- No gameplay advantage — only changes how your reticle looks
- Works by writing to `content/pkg_user/`, the user content directory built into War Thunder's [CDK mod system](https://wiki.warthunder.com/cdk/6498-user-mods-installation)
- Fully reversible with one click (Restore Game Default)

---

## Requirements

- **Python** (3.8 or newer) — [python.org](https://www.python.org/downloads/)
- **War Thunder** installed anywhere (auto-detected via Steam; if that fails, use the `…` button in the editor to browse to your War Thunder folder manually)
- The `zstandard` package — **auto-installs on first run**, no manual steps needed

---

## Setup

1. Install Python if you haven't already — make sure to check **"Add Python to PATH"** during install
2. Download this repo and unzip it anywhere
3. Double-click `Run Editor.bat`

That's it. On first launch it will find your War Thunder directory automatically via Steam and install `zstandard` if needed.

---

## How to Use

### Drawing your reticle
- **Add Line / Add Ellipse** — select a tool, then click and drag on the canvas
- **Select** tool — click an element to select it, drag to move
- **Rubber-band select** — drag on empty canvas to select multiple elements, then move them together
- **Scroll wheel** — zoom in/out
- **Middle mouse drag** — pan the canvas
- **Delete key** — remove selected element(s)
- **Color picker** — sets the color for the entire reticle

### Coordinate system
The canvas represents the game's VECTOR_CANVAS space. The center (0, 0) is the aim point. Y units are 2× larger than X units on screen — an ellipse with equal X/Y radii will appear twice as tall as wide. Use half the Y value to draw circles.

### Exporting to game
1. Click **Export → War Thunder**
2. Launch War Thunder
3. Your reticle appears in helicopter rocket sight mode


### Restoring defaults
Click **Restore Game Default** — this deletes the `content/pkg_user/` folder and its associated files entirely, returning the game to factory state.

### Profiles
Save and load reticle designs as JSON files using the Profiles panel. Profiles are stored in a `profiles/` subfolder next to the editor.

---

## After a Game Update

The editor reads the reticle function directly from the live game files every time you export, so minor updates are handled automatically.

If a major update breaks things, the symptom will be an error on export like `helicopterRocketSightMode not found`. Open an issue and I'll push a fix.

---

## How It Works (Technical)

1. Decodes `gui.vromfs.bin` to read the current `airhudelems.nut`
2. Patches the `helicopterRocketSightMode()` function with your custom vectors
3. Repacks into `content/pkg_user/base.vromfs.bin` (VRFs format)

The game's asset loader gives `pkg_user` priority over base files, so your version loads instead. The original game files are never modified.
