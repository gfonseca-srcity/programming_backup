document.addEventListener('DOMContentLoaded', function(){
  const mode = document.getElementById('mode');
  const woidsLabel = document.getElementById('woidsLabel');
  const submittoLabel = document.getElementById('submittoLabel');
  const form = document.getElementById('woForm');
  const result = document.getElementById('result');
  const link = document.getElementById('gmaps_link');

  mode.addEventListener('change', ()=>{
    if(mode.value === 'ids'){
      woidsLabel.style.display = '';
      submittoLabel.style.display = 'none';
    } else {
      woidsLabel.style.display = 'none';
      submittoLabel.style.display = '';
    }
  });

  form.addEventListener('submit', async (e)=>{
    e.preventDefault();
    result.textContent = 'Running...';
    const data = { mode: mode.value };
    const starting = document.getElementById('starting').value.trim();
    if(starting) data.starting_address = starting;
    if(mode.value === 'ids'){
      const w = document.getElementById('woids').value.split(',').map(s=>s.trim()).filter(Boolean);
      data.woids = w;
    } else {
      const s = document.getElementById('submitto').value.trim();
      data.submitto = s;
    }

    try{
      const resp = await fetch('/shortestwopath/api/shortestpath', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data)});
      const text = await resp.text();
      let json = null;
      if(text){
        try{ json = JSON.parse(text); }catch(parseErr){
          result.textContent = `Error parsing JSON response: ${parseErr}. Response text:\n` + text;
          return;
        }
      }

      if(!resp.ok){
        const msg = (json && (json.detail || json.title)) || text || resp.statusText;
        result.textContent = 'Server error: ' + msg;
        return;
      }

      if(!json){
        result.textContent = 'No JSON returned from server.';
        return;
      }

      result.innerHTML = '<pre>' + JSON.stringify(json, null, 2) + '</pre>';
      if(json['googleMapsLink']){
        link.textContent = json['googleMapsLink'];
        link.href = json['googleMapsLink'];
        result.innerHTML += `<p><a href="${json.googleMapsLink}" target="_blank">Open route in Google Maps</a></p>`;
      } else {
        link.textContent = '';
        link.removeAttribute('href');
      }
    }catch(err){
      result.textContent = 'Error: ' + err;
    }
  });
});