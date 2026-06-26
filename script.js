const splits = {
  train: {
    label: "Training split",
    title: "581,765 image pairs",
    body:
      "Combines 240,226 ZOD pairs with 341,540 Mapillary pairs to scale supervised learning with both accurate and diverse data.",
    goal: "Large-scale training",
    source: "ZOD + Mapillary"
  },
  val: {
    label: "Validation split",
    title: "14,756 image pairs",
    body:
      "Curated validation data for model selection and reliable development against accurate pose annotations.",
    goal: "Development and model selection",
    source: "Curated ground-aerial pairs"
  },
  cross: {
    label: "Cross-area test split",
    title: "18,504 image pairs",
    body:
      "ZOD-derived evaluation data from geographic regions that do not overlap with training coverage.",
    goal: "Generalization to unseen areas",
    source: "Curated ZOD sequences"
  },
  snow: {
    label: "Snowy test split",
    title: "3,015 image pairs",
    body:
      "Selected snowy samples with verified alignment to test robustness under seasonal appearance changes.",
    goal: "Weather and seasonal robustness",
    source: "Curated ZOD sequences"
  },
  wild: {
    label: "In-the-wild test split",
    title: "1,361 image pairs",
    body:
      "Manually verified Mapillary evaluation data with diverse viewpoints, sensors, and capture conditions.",
    goal: "Unconstrained real-world evaluation",
    source: "Curated Mapillary images"
  }
};

const splitPanel = document.querySelector("#split-panel");
const splitTabs = document.querySelectorAll(".split-tab");
const groundSamples = document.querySelector("[data-ground-samples]");

function renderSplit(splitKey, tab) {
  const split = splits[splitKey];
  if (!split || !splitPanel) return;

  splitPanel.setAttribute("aria-labelledby", tab.id);
  splitPanel.innerHTML = `
    <p class="panel-label">${split.label}</p>
    <h3>${split.title}</h3>
    <p>${split.body}</p>
    <dl>
      <div>
        <dt>Primary goal</dt>
        <dd>${split.goal}</dd>
      </div>
      <div>
        <dt>Ground source</dt>
        <dd>${split.source}</dd>
      </div>
    </dl>
  `;
}

splitTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    splitTabs.forEach((candidate) => {
      candidate.classList.remove("is-active");
      candidate.setAttribute("aria-selected", "false");
    });
    tab.classList.add("is-active");
    tab.setAttribute("aria-selected", "true");
    renderSplit(tab.dataset.split, tab);
  });
});

async function renderGroundSamples() {
  if (!groundSamples) return;

  try {
    const response = await fetch("assets/img/dataset-ground-views/manifest.json?v=dataset-v2");
    if (!response.ok) return;

    const manifest = await response.json();
    const samples = Array.isArray(manifest.samples) ? manifest.samples : [];
    const svgNamespace = "http://www.w3.org/2000/svg";
    const locatorLayer = document.createElementNS(svgNamespace, "svg");
    locatorLayer.setAttribute("class", "hero-location-lines");
    locatorLayer.setAttribute("viewBox", "0 0 100 100");
    locatorLayer.setAttribute("preserveAspectRatio", "none");
    locatorLayer.setAttribute("focusable", "false");
    locatorLayer.setAttribute("aria-hidden", "true");

    samples.forEach((sample) => {
      const cardX = sample.card_x ?? sample.x;
      const cardY = sample.card_y ?? sample.y;
      const priorityClass = sample.priority === "secondary" ? "is-secondary" : "is-primary";

      const line = document.createElementNS(svgNamespace, "line");
      line.setAttribute("class", `location-line ${priorityClass}`);
      line.setAttribute("x1", sample.x);
      line.setAttribute("y1", sample.y);
      line.setAttribute("x2", cardX);
      line.setAttribute("y2", cardY);
      locatorLayer.append(line);

      const dot = document.createElementNS(svgNamespace, "circle");
      dot.setAttribute("class", `location-dot ${priorityClass}`);
      dot.setAttribute("cx", sample.x);
      dot.setAttribute("cy", sample.y);
      dot.setAttribute("r", "0.42");
      locatorLayer.append(dot);
    });

    groundSamples.replaceChildren(
      locatorLayer,
      ...samples.map((sample, index) => {
        const frame = document.createElement("figure");
        frame.className = `ground-sample ${sample.priority === "secondary" ? "is-secondary" : "is-primary"}`;
        frame.style.setProperty("--x", `${sample.card_x ?? sample.x}%`);
        frame.style.setProperty("--y", `${sample.card_y ?? sample.y}%`);
        frame.style.setProperty("--r", `${sample.rotation}deg`);
        frame.style.setProperty("--d", `${index * 34}ms`);

        const image = document.createElement("img");
        image.src = sample.src;
        image.alt = "";
        image.loading = "eager";
        image.decoding = "async";
        frame.append(image);
        return frame;
      })
    );
  } catch {
    groundSamples.replaceChildren();
  }
}

renderGroundSamples();

document.querySelectorAll("[aria-disabled='true']").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
  });
});

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const target = document.getElementById(button.dataset.copy);
    if (!target) return;

    const original = button.textContent;
    const text = target.textContent;
    try {
      if (navigator.clipboard) {
        await navigator.clipboard.writeText(text);
      } else {
        throw new Error("Clipboard API unavailable");
      }
      button.textContent = "Copied";
    } catch {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(target);
      selection.removeAllRanges();
      selection.addRange(range);
      button.textContent = "Selected";
    }

    window.setTimeout(() => {
      button.textContent = original;
    }, 1600);
  });
});
