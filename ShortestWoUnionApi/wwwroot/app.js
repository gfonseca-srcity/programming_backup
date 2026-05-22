function renderResult(data, container) {
  container.innerHTML = '';
  // human lines (friendly summary)
  if (data?.human_lines && data.human_lines.length) {
    const humanDiv = document.createElement('div');
    humanDiv.className = 'human-lines';
    humanDiv.innerHTML = '<h3>Summary</h3>' + '<pre>' + escapeHtml(data.human_lines.join('\n')) + '</pre>';
    container.appendChild(humanDiv);
  } else if (data?.raw_stdout) {
    const humanDiv = document.createElement('div');
    humanDiv.className = 'human-lines';
    humanDiv.innerHTML = '<h3>Raw Output</h3>' + '<pre>' + escapeHtml(data.raw_stdout) + '</pre>';
    container.appendChild(humanDiv);
  }

  // structured components
  if (data?.components) {
    const comps = Array.isArray(data.components) ? data.components : (Array.from(data.components.EnumerateArray()).map(x => JSON.parse(x.GetRawText())));
    const listDiv = document.createElement('div');
    listDiv.className = 'components';
    listDiv.innerHTML = '<h3>Top Components</h3>';
    comps.forEach((c, idx) => {
      const card = document.createElement('div');
      card.className = 'component-card';
      const title = document.createElement('div'); title.className = 'component-title';
      title.textContent = `${idx+1}. ${c.main} — ${c.affected} affected`;
      const members = document.createElement('ul'); members.className = 'component-members';
      (c.members || []).forEach(m => { const li = document.createElement('li'); li.textContent = m; members.appendChild(li); });
      card.appendChild(title);
      card.appendChild(members);
      listDiv.appendChild(card);
    });
    container.appendChild(listDiv);
  }

  // raw stderr
  if (data?.raw_stderr) {
    const errDiv = document.createElement('div');
    errDiv.className = 'stderr';
    errDiv.innerHTML = '<h4>Diagnostics</h4><pre>' + escapeHtml(data.raw_stderr) + '</pre>';
    container.appendChild(errDiv);
  }
}

function escapeHtml(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

async function runGraphRequest(body, resultDiv) {
  resultDiv.textContent = 'Running...';
  try {
    const r = await fetch('/assetsdownstreamimpact/api/unionfind', { method: 'POST', headers: { 'Content-Type':'application/json' }, body: JSON.stringify(body) });
    const ct = r.headers.get('content-type') || '';
    if (!r.ok) {
      if (ct.includes('application/json')) {
        const data = await r.json();
        renderResult(data, resultDiv);
      } else {
        resultDiv.textContent = await r.text();
      }
      return;
    }
    const data = await r.json();
    renderResult(data, resultDiv);
  } catch (e) {
    resultDiv.textContent = 'Request failed: ' + e;
  }
}

document.getElementById('run').addEventListener('click', async () => {
  const asset = document.getElementById('asset').value;
  const k = parseInt(document.getElementById('k').value || '5', 10);
  let graphText = document.getElementById('graph').value;
  let graph;
  const resultDiv = document.getElementById('result');
  try { graph = JSON.parse(graphText); } catch (e) { resultDiv.textContent = 'Invalid JSON: ' + e; return; }
  const body = { nodes: graph, assetType: asset, k };
  await runGraphRequest(body, resultDiv);
});

// file input handling
const fileInput = document.getElementById('file');
if (fileInput) {
  fileInput.addEventListener('change', async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const text = await f.text();
    // try parse JSON
    const textarea = document.getElementById('graph');
    try {
      const parsed = JSON.parse(text);
      // if parsed likely has wrapper, try to extract nodes
      if (parsed && typeof parsed === 'object' && parsed.nodes) {
        textarea.value = JSON.stringify(parsed.nodes, null, 2);
      } else {
        textarea.value = JSON.stringify(parsed, null, 2);
      }
    } catch (err) {
      textarea.value = text;
    }

    const resultDiv = document.getElementById('result');
    const autoRun = document.getElementById('autoRun');
    if (autoRun && autoRun.checked) {
      // build body and run
      let graph;
      try { graph = JSON.parse(textarea.value); } catch { resultDiv.textContent = 'Invalid JSON from file'; return; }
      const asset = document.getElementById('asset').value;
      const k = parseInt(document.getElementById('k').value || '5', 10);
      const body = { nodes: graph, assetType: asset, k };
      await runGraphRequest(body, resultDiv);
    }
  });
}