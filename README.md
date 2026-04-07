# 3D Batch Optimizer for Blender (WebP Enhanced)

Batch optimize **GLB**, **GLTF**, and **VRM** files with smart texture downscaling and compression via Blender.

This is an enhanced version of [blender-3d-batch-optimizer](https://github.com/DOCTORdripp/blender-3d-batch-optimizer) by [DOCTORdripp](https://github.com/DOCTORdripp), with **WebP texture format support** added.

## What's New

- **WebP texture compression** — smaller files than JPEG with alpha channel support (requires Blender 3.4+)
- **Smart per-file format routing** — in a mixed batch, GLB/GLTF files get WebP while VRM files automatically fall back to JPEG (most VRM viewers don't support WebP)
- **Separate quality controls** for JPEG and WebP
- Fully compatible with **Blender 4.x and 5.x**

## How Format Selection Works

| File Type | AUTO Mode | WEBP Mode | JPEG Mode | PNG Mode |
|-----------|-----------|-----------|-----------|----------|
| `.glb` / `.gltf` | WebP | WebP | JPEG | PNG |
| `.vrm` | JPEG | JPEG (auto fallback) | JPEG | PNG |

Normal maps, roughness, and metallic textures always stay as PNG regardless of setting.

## Installation (Blender Addon)

1. Download `3d-batch-optimizer-blender_addon.zip` from this repo
2. Open Blender → **Edit** → **Preferences** → **Add-ons**
3. Click **Install** → select the zip file → enable **"3D Batch Optimizer"**
4. The panel appears in the 3D Viewport sidebar (**N** key → **3D Batch Optimizer** tab)

## Requirements

- **Blender 3.4+** (for WebP support — works with 4.x and 5.x)
- **VRM addon** (optional, only needed for `.vrm` files) — [Download here](https://vrm-addon-for-blender.info/en/)

## Usage

### Blender Addon (GUI)

Open the **3D Batch Optimizer** panel in the sidebar, set your input/output directories and options, then hit **OPTIMIZE FILES**.

### Command Line

```
blender --background --python glb_bulk_optimizer.py
```

Edit the configuration variables at the top of the script to set your directories and preferences.

## Credits

Based on [blender-3d-batch-optimizer](https://github.com/DOCTORdripp/blender-3d-batch-optimizer) by [DOCTORdripp](https://github.com/DOCTORdripp).

## License

This project is licensed under the same terms as the original — see [LICENSE](LICENSE) for details.
