#!/usr/bin/env python3
"""
GLTF/GLB/VRM Bulk Optimizer for Blender (WebP Enhanced)
Batch processes .gltf, .glb, and .vrm files to downscale textures and reduce file sizes.

Based on: https://github.com/DOCTORdripp/blender-3d-batch-optimizer
Enhanced with WebP texture format support for Blender 3.4+/4.x/5.x

Note: VRM files use JPEG instead of WebP, as most VRM viewers/engines
do not support WebP textures.

Usage:
    blender --background --python glb_bulk_optimizer.py

Requirements:
    - Blender 3.4+ with Python API (3.4+ required for WebP support)
    - VRM addon installed (for VRM file support)
    - Input and output directories configured below
"""

import bpy
import bmesh
import os
import sys
import traceback
from pathlib import Path
from mathutils import Vector
import tempfile
import shutil

# ================================
# CONFIGURATION VARIABLES
# ================================

# Input directory containing .glb files to process
INPUT_DIR = r"C:\Users\docto\Documents\GitHub\GLB-bulk-optimize\models"

# Output directory for processed .glb files
OUTPUT_DIR = r"C:\Users\docto\Documents\GitHub\GLB-bulk-optimize\models-optimized"

# Target texture resolution (width x height)
TARGET_RESOLUTION = 512

# Skip files that already exist in output directory
SKIP_EXISTING = True

# Texture compression format ('JPEG', 'PNG', 'WEBP', or 'AUTO')
# AUTO will use WEBP for GLB/GLTF (supports lossy compression and alpha),
# JPEG for VRM (VRM viewers don't support WebP),
# and PNG for normal maps and precision-critical textures
TEXTURE_FORMAT = 'AUTO'

# JPEG quality (1-100, used for JPEG textures and VRM fallback)
JPEG_QUALITY = 80

# WebP quality (1-100, used for GLB/GLTF textures)
WEBP_QUALITY = 80

# Preserve original file format (True = GLTF stays GLTF, False = convert GLTF to GLB)
PRESERVE_FORMAT = False

# Enable verbose logging
VERBOSE = True

# Remove specular tint textures and set specular to 0 (reduces file size)
REMOVE_SPECULAR = True

# Aggressive lossy conversion for PNG textures (except those needing alpha when using JPEG)
AGGRESSIVE_JPEG_CONVERSION = True

# Force compression even when not resizing (helps reduce file size)
FORCE_COMPRESSION = True

# ================================
# UTILITY FUNCTIONS
# ================================

def log(message, level="INFO"):
    """Print formatted log message."""
    print(f"[{level}] {message}")

def is_webp_supported():
    """Check if current Blender version supports WebP (3.4+)."""
    return bpy.app.version >= (3, 4, 0)

def clear_scene():
    """Clear all objects, materials, and images from the current scene."""
    try:
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        
        for block in bpy.data.meshes:
            bpy.data.meshes.remove(block)
        for block in bpy.data.materials:
            bpy.data.materials.remove(block)
        for block in bpy.data.images:
            bpy.data.images.remove(block)
        for block in bpy.data.textures:
            bpy.data.textures.remove(block)
        for block in bpy.data.node_groups:
            bpy.data.node_groups.remove(block)
            
        for collection in bpy.data.collections:
            bpy.data.collections.remove(collection)
            
        if VERBOSE:
            log("Scene cleared successfully")
            
    except Exception as e:
        log(f"Warning: Error clearing scene: {e}", "WARNING")

def has_alpha_channel(image):
    """Check if image actually uses alpha channel (has transparency)."""
    try:
        if not image or not image.pixels:
            return False
        
        if len(image.pixels) % 4 != 0:
            return False
        
        pixels = image.pixels[:]
        alpha_values = pixels[3::4]
        
        sample_step = max(1, len(alpha_values) // 1000)
        
        for i in range(0, len(alpha_values), sample_step):
            if alpha_values[i] < 0.98:
                if VERBOSE:
                    log(f"Alpha channel detected in '{image.name}' (alpha value: {alpha_values[i]:.3f})")
                return True
        
        return False
    except Exception as e:
        if VERBOSE:
            log(f"Warning: Could not analyze alpha channel for '{image.name if image else 'unknown'}': {e}")
        return False

def resolve_effective_format(texture_format, file_type):
    """
    Resolve the effective texture format for a given file type.

    VRM files never use WebP (most VRM viewers don't support it).
    When the user picks AUTO or WEBP but we're processing a VRM,
    the effective lossy format falls back to JPEG.

    Returns the effective format string to use for format decisions.
    """
    is_vrm = (file_type == 'vrm')

    if texture_format == 'WEBP' and is_vrm:
        return 'JPEG'
    if texture_format == 'AUTO' and is_vrm:
        return 'AUTO_VRM'
    return texture_format

def get_texture_format(image_name, node_type=None, image=None, effective_format='AUTO'):
    """
    Determine optimal texture format based on image type and actual usage.

    effective_format values:
        'PNG', 'JPEG', 'WEBP'  - forced format
        'AUTO'                  - smart selection, WebP preferred for lossy
        'AUTO_VRM'              - smart selection, JPEG preferred (VRM-safe)
    """
    if effective_format == 'PNG':
        return 'PNG'
    elif effective_format == 'JPEG':
        return 'JPEG'
    elif effective_format == 'WEBP':
        if is_webp_supported():
            return 'WEBP'
        return 'JPEG'

    # AUTO or AUTO_VRM - smart selection
    is_vrm_safe = (effective_format == 'AUTO_VRM')
    if is_vrm_safe or not is_webp_supported():
        lossy_format = 'JPEG'
    else:
        lossy_format = 'WEBP'

    name_lower = image_name.lower()

    # Normal maps, roughness, metallic need precision -> PNG always
    if any(keyword in name_lower for keyword in ['normal', 'nrm', 'bump', 'roughness', 'metallic']):
        return 'PNG'

    if AGGRESSIVE_JPEG_CONVERSION:
        if lossy_format == 'WEBP':
            return 'WEBP'
        else:
            if image and has_alpha_channel(image):
                if VERBOSE:
                    log(f"Keeping '{image_name}' as PNG due to alpha channel usage")
                return 'PNG'
            else:
                if image and image.file_format == 'PNG':
                    if VERBOSE:
                        log(f"Converting PNG '{image_name}' to JPEG (no alpha channel detected)")
                return 'JPEG'
    else:
        if any(keyword in name_lower for keyword in ['alpha', 'opacity', 'mask']):
            return 'PNG'

    return lossy_format

def clean_material_properties(material):
    """Remove specular tint and reduce specular to 0 for better compression."""
    if not REMOVE_SPECULAR:
        return
        
    try:
        if not material.use_nodes:
            if hasattr(material, 'specular_intensity'):
                material.specular_intensity = 0.0
            if hasattr(material, 'specular_color'):
                material.specular_color = (0.0, 0.0, 0.0)
            if VERBOSE:
                log(f"Set specular properties to 0 on non-node material: {material.name}")
            return
        
        nodes_to_remove = []
        links_to_remove = []
        
        if VERBOSE:
            for node in material.node_tree.nodes:
                if node.type == 'BSDF_PRINCIPLED':
                    for input_name in ['Specular', 'Specular IOR Level', 'Specular Tint']:
                        if input_name in node.inputs:
                            input_socket = node.inputs[input_name]
                            log(f"Material '{material.name}' - {input_name} current value: {input_socket.default_value}")
        
        for node in material.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                image_name_lower = node.image.name.lower()
                node_name_lower = node.name.lower()
                
                if any(keyword in image_name_lower for keyword in ['specular_tint', 'spectint', 'spec_tint', 'specular tint']) or \
                   any(keyword in node_name_lower for keyword in ['specular_tint', 'spectint', 'spec_tint', 'specular tint']):
                    if VERBOSE:
                        log(f"Removing specular tint texture: {node.image.name} (node: {node.name})")
                    for output in node.outputs:
                        for link in output.links:
                            links_to_remove.append(link)
                    nodes_to_remove.append(node)
            
            elif node.type == 'BSDF_PRINCIPLED':
                specular_inputs = []
                
                for input_name in ['Specular', 'Specular IOR Level', 'Specular Tint']:
                    if input_name in node.inputs:
                        specular_inputs.append(node.inputs[input_name])
                
                for specular_input in specular_inputs:
                    try:
                        for link in specular_input.links:
                            links_to_remove.append(link)
                        
                        if specular_input.name == 'Specular Tint':
                            if hasattr(specular_input, 'default_value'):
                                current_val = specular_input.default_value
                                if isinstance(current_val, (int, float)):
                                    specular_input.default_value = 1.0
                                else:
                                    specular_input.default_value = (1.0, 1.0, 1.0, 1.0)
                        else:
                            if hasattr(specular_input, 'default_value'):
                                if isinstance(specular_input.default_value, (int, float)):
                                    specular_input.default_value = 0.0
                                else:
                                    specular_input.default_value = (0.0, 0.0, 0.0, 1.0)
                        
                        if VERBOSE:
                            log(f"Set {specular_input.name} to {specular_input.default_value} on material: {material.name}")
                    
                    except Exception as e:
                        if VERBOSE:
                            log(f"Warning: Could not set {specular_input.name} on material {material.name}: {e}", "WARNING")
            
            elif node.type in ['BSDF_GLOSSY', 'BSDF_ANISOTROPIC']:
                if VERBOSE:
                    log(f"Found specular node type {node.type} in material {material.name} - marking for removal")
                nodes_to_remove.append(node)
        
        for link in links_to_remove:
            try:
                material.node_tree.links.remove(link)
            except:
                pass
        
        for node in nodes_to_remove:
            try:
                node_name = node.name
                material.node_tree.nodes.remove(node)
                if VERBOSE:
                    log(f"Successfully removed node '{node_name}' from material '{material.name}'")
            except Exception as e:
                if VERBOSE:
                    log(f"Warning: Could not remove node '{node.name}' from material '{material.name}': {e}", "WARNING")
            
    except Exception as e:
        log(f"Warning: Error cleaning material properties for '{material.name}': {e}", "WARNING")

def apply_texture_compression(image, target_format):
    """Apply texture compression by setting format and compressing via file save/reload."""
    try:
        if not image:
            return
        
        original_format = image.file_format
        was_packed = image.packed_file is not None
        
        if target_format in ('JPEG', 'WEBP'):
            quality = WEBP_QUALITY if target_format == 'WEBP' else JPEG_QUALITY
            ext = '.webp' if target_format == 'WEBP' else '.jpg'
            
            if VERBOSE:
                log(f"Converting '{image.name}' to {target_format} format (quality: {quality}%)")
            
            image.file_format = target_format
            
            if FORCE_COMPRESSION or original_format != target_format:
                try:
                    temp_dir = tempfile.gettempdir()
                    safe_name = "".join(c for c in image.name if c.isalnum() or c in ('_', '-'))
                    temp_file = os.path.join(temp_dir, f"temp_{safe_name}{ext}")
                    
                    original_quality = bpy.context.scene.render.image_settings.quality
                    original_render_format = bpy.context.scene.render.image_settings.file_format
                    
                    bpy.context.scene.render.image_settings.file_format = target_format
                    bpy.context.scene.render.image_settings.quality = quality
                    
                    image.filepath_raw = temp_file
                    image.save_render(temp_file)
                    
                    bpy.context.scene.render.image_settings.quality = original_quality
                    bpy.context.scene.render.image_settings.file_format = original_render_format
                    
                    image.filepath = temp_file
                    image.source = 'FILE'
                    image.reload()
                    
                    if was_packed:
                        image.pack()
                    
                    try:
                        os.remove(temp_file)
                    except:
                        pass
                    
                    if VERBOSE:
                        log(f"Successfully compressed '{image.name}' to {target_format} with quality {quality}%")
                        
                except Exception as e:
                    if VERBOSE:
                        log(f"Warning: Could not save/reload compress '{image.name}': {e}")
                        
        elif target_format == 'PNG':
            image.file_format = 'PNG'
            if VERBOSE:
                log(f"Keeping '{image.name}' as PNG format")
        
        image.update()
        
    except Exception as e:
        log(f"Warning: Error applying compression to '{image.name}': {e}", "WARNING")

def resize_image(image, target_width, target_height):
    """Resize a Blender image to target dimensions."""
    try:
        if image.size[0] <= target_width and image.size[1] <= target_height:
            if VERBOSE:
                log(f"Image '{image.name}' already at or below target resolution ({image.size[0]}x{image.size[1]})")
            return False
            
        if VERBOSE:
            log(f"Resizing '{image.name}' from {image.size[0]}x{image.size[1]} to {target_width}x{target_height}")
        
        image.scale(target_width, target_height)
        image.update()
        return True
        
    except Exception as e:
        log(f"Error resizing image '{image.name}': {e}", "ERROR")
        return False

def process_material_textures(material, effective_format):
    """Process all textures in a material."""
    if not material.use_nodes:
        return 0
    
    processed_count = 0
    
    for node in material.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image:
            image = node.image
            
            if hasattr(image, '_bulk_processed'):
                continue
            
            image_name_lower = image.name.lower()
            node_name_lower = node.name.lower()
            if any(keyword in image_name_lower for keyword in ['specular_tint', 'spectint', 'spec_tint', 'specular tint']) or \
               any(keyword in node_name_lower for keyword in ['specular_tint', 'spectint', 'spec_tint', 'specular tint']):
                if VERBOSE:
                    log(f"Skipping specular tint texture that should have been removed: {image.name}")
                continue
                
            original_packed = image.packed_file is not None
            
            target_format = get_texture_format(image.name, node.type, image, effective_format)
            
            apply_texture_compression(image, target_format)
            
            processed_count += 1
            
            if image.size[0] > TARGET_RESOLUTION or image.size[1] > TARGET_RESOLUTION:
                if VERBOSE:
                    log(f"Resizing '{image.name}' from {image.size[0]}x{image.size[1]} to {TARGET_RESOLUTION}x{TARGET_RESOLUTION}")
                
                image.scale(TARGET_RESOLUTION, TARGET_RESOLUTION)
                image.update()
                
                if VERBOSE:
                    log(f"Successfully resized texture '{image.name}'")
            else:
                if VERBOSE:
                    log(f"Image '{image.name}' already at or below target resolution ({image.size[0]}x{image.size[1]})")
            
            if original_packed and not image.packed_file:
                try:
                    image.pack()
                    if VERBOSE:
                        log(f"Re-packed texture '{image.name}' for embedding")
                except Exception as e:
                    log(f"Warning: Could not re-pack texture '{image.name}': {e}", "WARNING")
            
            image['_bulk_processed'] = True
    
    return processed_count

def get_file_type(filepath):
    """Determine file type based on extension."""
    ext = filepath.suffix.lower()
    if ext in ['.glb', '.gltf']:
        return 'gltf'
    elif ext == '.vrm':
        return 'vrm'
    else:
        return 'unknown'

def import_file(input_path):
    """Import file based on its type."""
    file_type = get_file_type(input_path)
    
    if file_type == 'gltf':
        try:
            bpy.ops.import_scene.gltf(filepath=str(input_path))
            if VERBOSE:
                log(f"Imported GLTF/GLB file: {input_path.name}")
            return True
        except Exception as e:
            log(f"Error importing GLTF/GLB file '{input_path}': {e}", "ERROR")
            if "bone" in str(e).lower() or "animation" in str(e).lower():
                log(f"Animation/bone error - attempting to continue without animations", "WARNING")
                try:
                    bpy.ops.import_scene.gltf(filepath=str(input_path), import_pack_images=True)
                    if VERBOSE:
                        log(f"Imported GLTF/GLB file without animations: {input_path.name}")
                    return True
                except Exception as e2:
                    log(f"Failed to import even without animations: {e2}", "ERROR")
                    return False
            else:
                return False
    
    elif file_type == 'vrm':
        try:
            bpy.ops.import_scene.vrm(filepath=str(input_path))
            if VERBOSE:
                log(f"Imported VRM file: {input_path.name}")
            return True
        except Exception as e:
            log(f"Error importing VRM file '{input_path}': {e}", "ERROR")
            return False
    
    else:
        log(f"Unsupported file type: {input_path.suffix}", "ERROR")
        return False

def export_file(output_path, file_type):
    """Export file based on desired output type."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        if file_type == 'vrm':
            try:
                bpy.ops.export_scene.vrm(filepath=str(output_path))
            except TypeError as e:
                if VERBOSE:
                    log(f"Using fallback VRM export parameters due to: {e}")
                bpy.ops.export_scene.vrm(filepath=str(output_path))
        
        elif file_type == 'gltf':
            try:
                bpy.ops.export_scene.gltf(
                    filepath=str(output_path),
                    export_format='GLTF_SEPARATE',
                    export_materials='EXPORT',
                    export_colors=True,
                    export_cameras=False,
                    export_lights=False,
                    export_animations=True,
                    export_yup=True,
                    export_apply=False,
                    export_texcoords=True,
                    export_normals=True,
                    export_draco_mesh_compression_enable=False,
                    export_tangents=False,
                    use_selection=False,
                    use_visible=False,
                    use_renderable=False,
                    use_active_collection=False,
                    use_active_scene=False
                )
            except TypeError as e:
                if VERBOSE:
                    log(f"Using fallback GLTF export parameters due to: {e}")
                bpy.ops.export_scene.gltf(
                    filepath=str(output_path),
                    export_format='GLTF_SEPARATE'
                )
        
        else:
            try:
                bpy.ops.export_scene.gltf(
                    filepath=str(output_path),
                    export_format='GLB',
                    export_materials='EXPORT',
                    export_colors=True,
                    export_cameras=False,
                    export_lights=False,
                    export_animations=True,
                    export_yup=True,
                    export_apply=False,
                    export_texcoords=True,
                    export_normals=True,
                    export_draco_mesh_compression_enable=False,
                    export_tangents=False,
                    use_selection=False,
                    use_visible=False,
                    use_renderable=False,
                    use_active_collection=False,
                    use_active_scene=False
                )
            except TypeError as e:
                if VERBOSE:
                    log(f"Using fallback GLB export parameters due to: {e}")
                bpy.ops.export_scene.gltf(
                    filepath=str(output_path),
                    export_format='GLB'
                )
        
        return True
        
    except Exception as e:
        log(f"Error exporting file '{output_path}': {e}", "ERROR")
        return False

def process_glb_file(input_path, output_path):
    """Process a single 3D file (GLB/GLTF/VRM)."""
    try:
        log(f"Processing: {input_path.name}")
        
        clear_scene()
        
        if not import_file(input_path):
            return False
        
        # Determine file type and resolve effective texture format
        file_type = get_file_type(input_path)
        effective_format = resolve_effective_format(TEXTURE_FORMAT, file_type)
        
        if VERBOSE:
            if file_type == 'vrm' and TEXTURE_FORMAT in ('WEBP', 'AUTO'):
                log(f"VRM file detected — using JPEG instead of WebP for texture compression")
            log(f"Effective texture format: {effective_format}")
        
        # Clean up materials
        if REMOVE_SPECULAR:
            for material in bpy.data.materials:
                if material.users > 0:
                    clean_material_properties(material)
        
        # Process materials and textures
        total_textures_processed = 0
        processed_materials = 0
        
        for material in bpy.data.materials:
            if material.users > 0:
                texture_count = process_material_textures(material, effective_format)
                if texture_count > 0:
                    processed_materials += 1
                    total_textures_processed += texture_count
        
        log(f"Processed {total_textures_processed} textures across {processed_materials} materials")
        
        # Export
        input_file_type = get_file_type(input_path)
        
        if input_file_type == 'vrm':
            success = export_file(output_path, 'vrm')
        elif PRESERVE_FORMAT and input_file_type == 'gltf' and output_path.suffix.lower() == '.gltf':
            success = export_file(output_path, 'gltf')
        else:
            success = export_file(output_path, 'glb')
        
        if success:
            log(f"Successfully exported: {output_path.name}")
            return True
        else:
            return False
            
    except Exception as e:
        log(f"Error processing GLTF/GLB file '{input_path}': {e}", "ERROR")
        traceback.print_exc()
        return False

def get_file_size_mb(filepath):
    """Get file size in megabytes."""
    try:
        return os.path.getsize(filepath) / (1024 * 1024)
    except:
        return 0

def main():
    """Main processing function."""
    log("Starting GLTF/GLB/VRM Bulk Optimizer (WebP Enhanced)")
    log(f"Blender version: {bpy.app.version_string}")
    log(f"Input directory: {INPUT_DIR}")
    log(f"Output directory: {OUTPUT_DIR}")
    log(f"Target resolution: {TARGET_RESOLUTION}x{TARGET_RESOLUTION}")
    log(f"Texture format: {TEXTURE_FORMAT}")
    if TEXTURE_FORMAT in ('WEBP', 'AUTO'):
        if is_webp_supported():
            log(f"WebP support: AVAILABLE (Blender {bpy.app.version_string})")
        else:
            log(f"WebP support: NOT AVAILABLE (requires Blender 3.4+, falling back to JPEG)")
    if TEXTURE_FORMAT in ('WEBP', 'AUTO'):
        log("VRM files: will use JPEG (VRM viewers don't support WebP)")
    if REMOVE_SPECULAR:
        log("Specular removal: ENABLED")
    if AGGRESSIVE_JPEG_CONVERSION:
        log("Aggressive lossy conversion: ENABLED")
    if FORCE_COMPRESSION:
        log("Force compression: ENABLED")
    if TEXTURE_FORMAT in ['AUTO', 'JPEG']:
        log(f"JPEG quality: {JPEG_QUALITY}%")
    if TEXTURE_FORMAT in ['AUTO', 'WEBP']:
        log(f"WebP quality: {WEBP_QUALITY}%")
    
    # Validate directories
    input_path = Path(INPUT_DIR)
    output_path = Path(OUTPUT_DIR)
    
    if not input_path.exists():
        log(f"Error: Input directory does not exist: {INPUT_DIR}", "ERROR")
        return
    
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Find all .glb, .gltf, and .vrm files
    glb_files = (list(input_path.glob("*.glb")) + list(input_path.glob("*.GLB")) + 
                 list(input_path.glob("*.gltf")) + list(input_path.glob("*.GLTF")) +
                 list(input_path.glob("*.vrm")) + list(input_path.glob("*.VRM")))
    
    if not glb_files:
        log("No .glb, .gltf, or .vrm files found in input directory", "WARNING")
        return
    
    log(f"Found {len(glb_files)} .glb/.gltf/.vrm files to process")
    
    # Count file types for info
    vrm_count = sum(1 for f in glb_files if f.suffix.lower() == '.vrm')
    non_vrm_count = len(glb_files) - vrm_count
    if vrm_count > 0 and non_vrm_count > 0 and TEXTURE_FORMAT in ('AUTO', 'WEBP'):
        log(f"Mixed batch: {non_vrm_count} GLB/GLTF (-> WebP) + {vrm_count} VRM (-> JPEG)")
    
    # Process each file
    processed_count = 0
    skipped_count = 0
    error_count = 0
    total_size_before = 0
    total_size_after = 0
    
    for i, glb_file in enumerate(glb_files, 1):
        log(f"\n--- Processing file {i}/{len(glb_files)} ---")
        
        input_type = get_file_type(glb_file)
        if input_type == 'vrm':
            output_filename = glb_file.stem + '.vrm'
        elif PRESERVE_FORMAT:
            output_filename = glb_file.name
        else:
            output_filename = glb_file.stem + '.glb'
        
        output_file = output_path / output_filename
        
        if SKIP_EXISTING and output_file.exists():
            log(f"Skipping existing file: {output_file.name}")
            skipped_count += 1
            continue
        
        original_size = get_file_size_mb(glb_file)
        total_size_before += original_size
        
        success = process_glb_file(glb_file, output_file)
        
        if success:
            processed_count += 1
            new_size = get_file_size_mb(output_file)
            total_size_after += new_size
            compression_ratio = ((original_size - new_size) / original_size * 100) if original_size > 0 else 0
            log(f"Size: {original_size:.2f}MB -> {new_size:.2f}MB ({compression_ratio:+.1f}%)")
        else:
            error_count += 1
    
    # Final summary
    log(f"\n{'='*50}")
    log("PROCESSING COMPLETE")
    log(f"{'='*50}")
    log(f"Total files found: {len(glb_files)}")
    log(f"Successfully processed: {processed_count}")
    log(f"Skipped (already exist): {skipped_count}")
    log(f"Errors: {error_count}")
    
    if processed_count > 0:
        overall_compression = ((total_size_before - total_size_after) / total_size_before * 100) if total_size_before > 0 else 0
        log(f"Total size reduction: {total_size_before:.2f}MB -> {total_size_after:.2f}MB ({overall_compression:+.1f}%)")

if __name__ == "__main__":
    try:
        import bpy
    except ImportError:
        print("Error: This script must be run within Blender")
        print("Usage: blender --background --python glb_bulk_optimizer.py")
        sys.exit(1)
    
    bpy.context.scene.cycles.device = 'CPU'
    
    main()
