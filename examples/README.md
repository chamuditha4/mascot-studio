# Example assets

Two complete jobs, each with the green-screen source video and the sprite
sheet plus metadata that Mascot Studio produced from it. Use them to try the
tool without shooting anything yourself.

| Example | Source | Output | Frames | Sheet |
|---------|--------|--------|--------|-------|
| Celebrating | [Celebrating.mp4](Celebrating/Celebrating.mp4) | [Celebrating.png](Celebrating/Celebrating.png) + [.json](Celebrating/Celebrating.json) | 40 @ 10fps | 2842×4092 |
| Concerned | [Concerned.mp4](Concerned/Concerned.mp4) | [Concerned.png](Concerned/Concerned.png) + [.json](Concerned/Concerned.json) | 40 @ 10fps | 2016×3954 |

<table>
<tr><th>Celebrating, source then result</th><th>Concerned, source then result</th></tr>
<tr>
<td align="center">
<img src="preview/celebrating-source.webp" width="130" alt="Celebrating source">
<img src="preview/celebrating-cutout.webp" width="130" alt="Celebrating cutout">
</td>
<td align="center">
<img src="preview/concerned-source.webp" width="130" alt="Concerned source">
<img src="preview/concerned-cutout.webp" width="130" alt="Concerned cutout">
</td>
</tr>
</table>

The `preview/` directory holds only these README animations, downscaled,
compressed, and flattened onto a checkerboard to show where the alpha is.
Use the full-resolution files above for anything real.

## Try the processing pipeline

Upload the `.mp4` on the home page. This runs the full matting pass, so the
first run downloads the model and takes a few minutes on CPU.

## Try the editor without waiting

Import the finished sheet instead. Upload the `.png` and its `.json` on the
import panel and you land straight in the frame editor with all 40 frames
loaded, ready to erase, re-run, and re-export.

Note that re-exporting an imported sheet can shift the frame size by a pixel
or two. Export re-computes the union bounding box across the frames it is
given, and a sheet that was already cropped once crops slightly differently
the second time.

## Reuse

These assets are covered by the repository's [MIT license](../LICENSE) along
with the rest of the project, so feel free to use them for testing.
