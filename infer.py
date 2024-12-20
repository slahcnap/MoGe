import os
os.environ['OPENCV_IO_ENABLE_OPENEXR'] = '1'
import click
from pathlib import Path
from typing import *
import itertools
import json
import warnings

import cv2
import numpy as np
import torch
from PIL import Image
from tqdm import tqdm
import trimesh
import trimesh.visual

from moge.model import MoGeModel
from moge.utils.vis import colorize_depth
import utils3d


def save_maps(
    save_path: Union[str, os.PathLike], 
    points: np.ndarray, 
    depth: np.ndarray, 
    mask: np.ndarray, 
    intrinsics: np.ndarray, 
    image: np.ndarray
):
    cv2.imwrite(str(save_path / 'image.jpg'), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(save_path / 'depth.png'), cv2.cvtColor(colorize_depth(depth), cv2.COLOR_RGB2BGR))
    # cv2.imwrite(str(save_path / 'depth.exr'), depth, [cv2.IMWRITE_EXR_TYPE, cv2.IMWRITE_EXR_TYPE_FLOAT])
    # cv2.imwrite(str(save_path / 'mask.png'), (mask * 255).astype(np.uint8))
    # cv2.imwrite(str(save_path / 'points.exr'), cv2.cvtColor(points, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_EXR_TYPE, cv2.IMWRITE_EXR_TYPE_FLOAT])
    fov_x, fov_y = utils3d.numpy.intrinsics_to_fov(intrinsics)
    with open(save_path / 'fov.json', 'w') as f:
        json.dump({
            'fov_x': round(float(np.rad2deg(fov_x)), 2),
            'fov_y': round(float(np.rad2deg(fov_y)), 2),
        }, f)

def save_glb(
    save_path: Union[str, os.PathLike], 
    vertices: np.ndarray, 
    faces: np.ndarray, 
    vertex_uvs: np.ndarray,
    texture: np.ndarray,
):
    
    trimesh.Trimesh(
        vertices=vertices, 
        faces=faces, 
        visual = trimesh.visual.texture.TextureVisuals(
            uv=vertex_uvs, 
            material=trimesh.visual.material.PBRMaterial(
                baseColorTexture=Image.fromarray(texture),
                metallicFactor=0.5,
                roughnessFactor=1.0
            )
        ),
        process=False
    ).export(save_path / 'mesh.glb')


def save_ply(
    save_path: Union[str, os.PathLike], 
    vertices: np.ndarray, 
    faces: np.ndarray, 
    vertex_colors: np.ndarray,
):

    trimesh.Trimesh(
        vertices=vertices, 
        faces=faces, 
        vertex_colors=vertex_colors,
        process=False
    ).export(save_path / 'mesh.ply')


@click.command(help='Inference script for the MoGe model.')
@click.option('--input', 'input_path', type=click.Path(exists=True), help='Input image or folder path. "jpg" and "png" are supported.')
@click.option('--output', 'output_path', type=click.Path(), help='Output folder path')
@click.option('--pretrained', 'pretrained_model_name_or_path', type=str, default='Ruicheng/moge-vitl', help='Pretrained model name or path. Default is "Ruicheng/moge-vitl"')
@click.option('--device', 'device_name', type=str, default='cuda', help='Device name (e.g. "cuda", "cuda:0", "cpu"). Default is "cuda"')
@click.option('--resize', 'resize_to', type=int, default=None, help='Resize the image(s) & output maps to a specific size. Default is None (no resizing).')
@click.option('--resolution_level', type=int, default=9, help='An integer [0-9] for the resolution level of inference. The higher, the better but slower. Default is 9. Note that it is irrelevant to the output resolution.')
@click.option('--threshold', type=float, default=0.02, help='Threshold for removing edges. Default is 0.02. Smaller value removes more edges. "inf" means no thresholding.')
@click.option('--maps', 'save_maps_', is_flag=True, help='Whether to save the output maps and fov(image, depth, mask, points, fov).')
@click.option('--glb', 'save_glb_', is_flag=True, help='Whether to save the output as a.glb file. The color will be saved as a texture.')
@click.option('--ply', 'save_ply_', is_flag=True, help='Whether to save the output as a.ply file. The color will be saved as vertex colors.')
@click.option('--show', 'show', is_flag=True, help='Whether show the output in a window. Note that this requires pyglet<2 installed as required by trimesh.')

def main(
    input_path: str,
    output_path: str,
    pretrained_model_name_or_path: str,
    device_name: str,
    resize_to: int,
    resolution_level: int,
    threshold: float,
    save_maps_: bool,
    save_glb_: bool,
    save_ply_: bool,
    show: bool,
):  
    device = torch.device(device_name)

    include_suffices = ['jpg', 'png', 'jpeg', 'JPG', 'PNG', 'JPEG']
    if Path(input_path).is_dir():
        image_paths = sorted(itertools.chain(*(Path(input_path).rglob(f'*.{suffix}') for suffix in include_suffices)))
    else:
        image_paths = [Path(input_path)]
    
    if len(image_paths) == 0:
        raise FileNotFoundError(f'No image files found in {input_path}')

    model = MoGeModel.from_pretrained(pretrained_model_name_or_path).to(device).eval()

    for image_path in (pbar := tqdm(image_paths, desc='Inference', disable=len(image_paths) <= 1)):

        image = cv2.cvtColor(cv2.imread(str(image_path)), cv2.COLOR_BGR2RGB)
        height, width = image.shape[:2]
        if resize_to is not None:
            height, width = min(resize_to, int(resize_to * height / width)), min(resize_to, int(resize_to * width / height))
            image = cv2.resize(image, (width, height), cv2.INTER_AREA)
        image_tensor = torch.tensor(image / 255, dtype=torch.float32, device=device).permute(2, 0, 1)

        # Inference
        output = model.infer(image_tensor)
        points, depth, mask, intrinsics = output['points'].cpu().numpy(), output['depth'].cpu().numpy(), output['mask'].cpu().numpy(), output['intrinsics'].cpu().numpy()
        
        # Write outputs
        # if not any([save_maps_, save_glb_, save_ply_]):
        #     warnings.warn('No output format specified. Please use "--maps", "--glb", or "--ply" to specify the output.')

        save_folder = str(image_path.name.split("_")[0])
        save_path = Path(f"{output_path}/{save_folder}")# Path(output_path) # , image_path.relative_to(input_path).parent, image_path.stem)
        save_path.mkdir(exist_ok=True, parents=True)

        
        if save_maps_:
            save_maps(save_path, points, depth, mask, intrinsics, image)

        np.save(rf"{save_path}\points.npy", points)

        # Export mesh & visulization
        # if save_glb_ or save_ply_ or show:
        # faces, vertices, vertex_colors, vertex_uvs = utils3d.numpy.image_mesh(
        #     points,
        #     image.astype(np.float32) / 255,
        #     utils3d.numpy.image_uv(width=width, height=height),
        #     mask=mask & ~utils3d.numpy.depth_edge(depth, rtol=threshold, mask=mask),
        #     tri=True
        # )

        our_ret, ret_for_glb = utils3d.numpy.image_mesh(
            points,
            image.astype(np.float32) / 255,
            utils3d.numpy.image_uv(width=width, height=height),
            mask=mask & ~utils3d.numpy.depth_edge(depth, rtol=threshold, mask=mask),
            tri=True
        )

        # When exporting the model, follow the OpenGL coordinate conventions:
        # - world coordinate system: x right, y up, z backward.
        # - texture coordinate system: (0, 0) for left-bottom, (1, 1) for right-top.
        faces, vertices, vertex_colors, vertex_uvs = our_ret
        faces_glb, vertices_glb, vertex_colors_glb, vertex_uvs_glb = ret_for_glb

        vertices, vertex_uvs = vertices * [1, -1, -1], vertex_uvs * [1, -1] + [0, 1]
        vertices_glb, vertex_uvs_glb = vertices_glb * [1, -1, -1], vertex_uvs_glb * [1, -1] + [0, 1]

        if save_glb_:
            save_glb(save_path, vertices_glb, faces_glb, vertex_uvs_glb, image)

        if save_ply_:
            save_ply(save_path, vertices_glb, faces_glb, vertex_colors_glb)

        if show:
            trimesh.Trimesh(
                vertices=vertices_glb,
                vertex_colors=vertex_colors_glb,
                faces=faces_glb, 
                process=False
            ).show()  

        fov_x, fov_y = utils3d.numpy.intrinsics_to_fov(intrinsics)
        with open(save_path / 'fov.json', 'w') as f:
            json.dump({
                'fov_x': round(float(np.rad2deg(fov_x)), 2),
                'fov_y': round(float(np.rad2deg(fov_y)), 2),
            }, f)

        np.save(str(save_path / 'faces.npy'), faces)
        np.save(str(save_path / 'vertices.npy'), vertices)
        np.save(str(save_path / 'vertex_colors.npy'), vertex_colors)
        np.save(str(save_path / 'vertex_uvs.npy'), vertex_uvs)


if __name__ == '__main__':
    main()