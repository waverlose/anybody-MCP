# AnyBody Native Animation Rendering Guide

## Overview

AnyBody Modeling System can render 3D animations headlessly via `AnyBodyCon.exe` using the built-in `AnyCamRecorder` class. This produces real骨骼+肌肉 rendering without the GUI.

## How It Works

### Pipeline

```
Model .main.any
    ↓ inject CameraClassTemplate.any
    ↓ inject VideoLookAtCamera into Main = { }
    ↓ run macro: RunApplication → Reset → StartTrigger → InverseDynamics → StopTrigger
    ↓ collect PNG frames from output directory
    ↓ post-process: overlay data / convert to GIF/MP4
Output: animation video
```

### Key Components

1. **CameraClassTemplate.any** — Located at `AMMR/Tools/ModelUtilities/Video/CameraClassTemplate.any`
   - Defines `#class_template VideoLookAtCamera` with AnyCamRecorder
   - Must be copied into the project directory (not included from AMMR path)

2. **VideoLookAtCamera** — Instance of the camera class inside `Main = { }`
   - Controls camera position, orientation, resolution, output path
   - Contains `Analysis` folder pointing to the operation to record

3. **AnyCamRecorder** — Built into CameraClassTemplate
   - Captures PNG frames at each simulation step
   - Frame naming: `{VideoName}_{Counter:04d}.png`
   - Key setting: `_CLEAN_UP_IMAGES = 0` to keep frames for post-processing

## Camera Parameters

### AnyBody Coordinate System (GaitVaughan)

**Important:** The GaitVaughan model's coordinate system is counter-intuitive:
- Person walks along **-Z axis** (forward)
- **X axis** = lateral (side to side)
- **Y axis** = small vertical offset (NOT the body's "up" direction)
- **Body's long axis (head-to-foot) is along Z, NOT Y**

This means when viewing from the X direction (side view), the body appears horizontal. This is the default AnyBody GUI behavior.

### VideoLookAtCamera Parameters

| Parameter | Description | GaitVaughan | Human Standing |
|-----------|-------------|-------------|----------------|
| `CameraDistance` | Distance from LookAtPoint to camera | 3 | 4 |
| `CameraLookAtPoint` | Point camera focuses on `{x, y, z}` | `{0, 0.6, 0.4}` | `{0, 0.8, 0}` |
| `CameraFieldOfView` | Vertical FOV in meters at LookAtPoint | 2 | 3 |
| `CameraDirection` | Direction FROM LookAtPoint TO camera | `{1, 0, 0}` | `{1, 0.3, 0}` |
| `UP_DIRECTION` | Which axis is up | `y` | `y` |
| `BackgroundColor` | Scene background color | `{0.95, 0.95, 0.95}` | `{1, 1, 1}` |
| `VideoResolution` | Output resolution | `{1920, 1080}` | `{1920, 1080}` |
| `_CLEAN_UP_IMAGES` | Keep PNG frames after recording | `0` (keep) | `0` (keep) |
| `CREATE_GIF` | Auto-create GIF (needs ffmpeg) | `0` (disabled) | `0` (disabled) |
| `FFMPEG_ON_PATH` | ffmpeg available in PATH | `0` (no) | `0` (no) |

### CameraDirection Explained

`CameraDirection` is the direction **from the LookAtPoint to the camera position**, not the direction the camera looks.

- `{1, 0, 0}` — Camera at +X side (right side view) — **recommended for GaitVaughan**
- `{0, 0, 1}` — Camera at +Z side (front view for gait) — **may not capture frames in headless mode**
- `{1, 0.2, 0}` — Camera at +X, slightly elevated (wider view)
- `{0.8, 0.4, 0.6}` — 3/4 perspective view

### Why Models Appear "Horizontal" in Side View

In AnyBody's GaitVaughan model, the person walks along the Z axis, and the body's long axis (head-to-foot) is also along Z. When viewing from the X direction (side view), the body's long axis maps to the image's horizontal axis, making the person appear to be lying down.

**This is the correct AnyBody behavior** — the model IS standing upright in world space, but the coordinate system is rotated relative to what you'd expect.

To see the body upright, you would need to view from the Y direction (top-down), but this is not useful for gait analysis.

## Known Limitations

### Arms Not Rendered
- Enabling arms requires adding GlenohumeralFlexion, GlenohumeralAbduction, etc. to Mannequin.any
- The arm model's `InitialPositions.any` reads from `Posture.Right` via `BodyModel.Right.JointPos`
- Without these variables, you get `Unresolved object` errors
- The GaitVaughan model doesn't include arm variables by default
- **Workaround:** Use the Human Standing template which has arm support

### Body Appears Horizontal
- This is a coordinate system limitation, not a camera issue
- The body's long axis is along Z, not Y
- From the X direction (side view), the body appears horizontal
- This matches the AnyBody GUI default view

### Front View May Not Capture Frames
- `CameraDirection = {0, 0, 1}` (front view) may not capture frames in headless mode
- The camera position relative to the model might cause rendering issues
- **Recommendation:** Use side view `{1, 0, 0}` which reliably captures frames

## Macro Commands for Recording

```
operation Main.RunApplication        # Run calibration + inverse dynamics
run
operation Main.AnimCam.Operations.Reset       # Reset camera counter
run
operation Main.AnimCam.Operations.StartTrigger # Start recording frames
run
operation Main.Study.InverseDynamics          # Run the analysis (frames captured per step)
run
operation Main.AnimCam.Operations.StopTrigger  # Stop recording
run
```

## Frame Post-Processing

### Overlay Joint Data
The `_overlay_values_on_frames()` function adds text panels showing:
- Joint angles (hip flexion/abduction/rotation, knee flexion, ankle flexion)
- Joint reaction forces (L5/S1, hip, knee, ankle)

### Format Conversion
- **GIF**: PIL/Pillow `Image.save(save_all=True)`
- **MP4**: `imageio.get_writer()` with `libx264` codec
- **BVH**: Exported separately via `export_animation_bvh()`

## Common Issues

### "No frame PNGs were captured"
- Check `_CLEAN_UP_IMAGES = 0` is set
- Check `VideoOutputPath` exists and is writable
- Check the model runs successfully (look for "Inverse dynamic analysis completed")
- Try side view `CameraDirection = {1, 0, 0}` instead of front view

### Frames are empty / white
- Camera is too far or FOV is too large
- CameraDirection is pointing away from the model
- Model didn't load correctly (check libdef.any path)

### Model appears rotated / wrong orientation
- CameraDirection and UP_DIRECTION may conflict
- For GaitVaughan: use `{1, 0, 0}` for side view (body will appear horizontal — this is correct)
- For Human Standing: use `{1, 0.3, 0}` for slightly elevated side view

### Arms cause "Unresolved object" errors
- The GaitVaughan model doesn't have arm variables in Mannequin.any
- Adding arm variables requires matching the exact structure expected by `InitialPositions.any`
- **Recommendation:** Use Human Standing template for arm rendering, or accept no arms in GaitVaughan

## File Locations

- CameraClassTemplate: `C:\Program Files\AnyBody Technology\AnyBody.7.3\AMMR\Tools/ModelUtilities/Video/CameraClassTemplate.any`
- Implementation: `src/anybody_runner.py` → `export_native_anybody_animation()`
- MCP tool: `src/anybody_mcp_server.py` → `export_native_animation()`
