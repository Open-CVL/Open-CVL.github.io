# OpenCVL Homepage

Static project homepage for the OpenCVL dataset.

## Files

- `index.html` contains the page content and release-link placeholders.
- `styles.css` contains the responsive layout and visual design.
- `script.js` powers the split tabs and citation copy button.
- `assets/img/` contains the cropped visual assets extracted from the submission PDF.
- `tools/build_hero_collage.py` regenerates the hero collage from a Dutch aerial
  patch and Mapillary thumbnails. It reads the Mapillary token from stdin and
  does not store the token in the repository.
- `tools/build_country_aerial_background.py` regenerates the four-country aerial
  background from city-scale patches for Stockholm, Amsterdam, Krakow, and Oslo.

Open `index.html` directly in a browser, or deploy the folder as a GitHub Pages site.
