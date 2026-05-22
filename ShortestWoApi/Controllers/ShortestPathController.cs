using System.Diagnostics;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc;

namespace ShortestWoApi.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    public class ShortestPathController : ControllerBase
    {
        private readonly ILogger<ShortestPathController> _logger;

        public ShortestPathController(ILogger<ShortestPathController> logger)
        {
            _logger = logger;
        }

        [HttpPost]
        public async Task<IActionResult> Post()
        {
            try
            {
                // Read raw body for diagnostic and tolerant parsing
                string bodyStr;
                using (var reader = new System.IO.StreamReader(Request.Body))
                {
                    bodyStr = await reader.ReadToEndAsync();
                }

                _logger.LogInformation("Raw request body: {body}", bodyStr);

                JsonDocument doc;
                try
                {
                    doc = JsonDocument.Parse(bodyStr);
                }
                catch (System.Text.Json.JsonException jsonEx)
                {
                    // Try a tolerant fallback: replace single quotes with double quotes and retry
                    var replaced = bodyStr.Replace("'", "\"");
                    try
                    {
                        doc = JsonDocument.Parse(replaced);
                    }
                    catch (System.Text.Json.JsonException)
                    {
                        _logger.LogWarning(jsonEx, "Failed to parse JSON body. Raw body: {body}", bodyStr);
                        return Problem(detail: $"Invalid JSON in request body. Raw body: {bodyStr}");
                    }
                }

                var body = doc.RootElement;

                string mode = body.TryGetProperty("mode", out var m) ? (m.ValueKind == JsonValueKind.String ? m.GetString() ?? "submitto" : "submitto") : "submitto";
                string? starting = body.TryGetProperty("starting_address", out var sa) ? (sa.ValueKind == JsonValueKind.String ? sa.GetString() : null) : null;

                var psi = new ProcessStartInfo
                {
                    FileName = "python",
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WorkingDirectory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "PycharmProjects", "CIS-Support")
                };

                psi.ArgumentList.Add("shortestWOPath.py");

                if (mode == "ids")
                {
                    if (body.TryGetProperty("woids", out var w) && w.ValueKind == JsonValueKind.Array)
                    {
                        var list = w.EnumerateArray().Select(x => x.GetString()).Where(s => s != null).Select(s => s!).ToArray();
                        psi.ArgumentList.Add("--woids");
                        psi.ArgumentList.Add(string.Join(',', list));
                    }
                    if (starting != null) { psi.ArgumentList.Add("--starting_address"); psi.ArgumentList.Add(starting); }
                }
                else
                {
                    string submitto = body.TryGetProperty("submitto", out var st) && st.ValueKind == JsonValueKind.String ? st.GetString() ?? "" : "";
                    if (!string.IsNullOrEmpty(submitto)) { psi.ArgumentList.Add("--submitto_name"); psi.ArgumentList.Add(submitto); }
                    if (starting != null) { psi.ArgumentList.Add("--starting_address"); psi.ArgumentList.Add(starting); }
                }

                using var proc = Process.Start(psi)!;

                var stdoutSb = new System.Text.StringBuilder();
                var stderrSb = new System.Text.StringBuilder();

                proc.EnableRaisingEvents = true;
                proc.OutputDataReceived += (s, e) =>
                {
                    if (e.Data == null) return;
                    lock (stdoutSb) { stdoutSb.AppendLine(e.Data); }
                    _logger.LogInformation("PYOUT: {line}", e.Data);
                };
                proc.ErrorDataReceived += (s, e) =>
                {
                    if (e.Data == null) return;
                    lock (stderrSb) { stderrSb.AppendLine(e.Data); }
                    _logger.LogWarning("PYERR: {line}", e.Data);
                };

                proc.BeginOutputReadLine();
                proc.BeginErrorReadLine();

                var tcs = new TaskCompletionSource<bool>();
                void ExitedHandler(object? s, EventArgs e) => tcs.TrySetResult(true);
                proc.Exited += ExitedHandler;

                var timeout = TimeSpan.FromSeconds(120);
                var finished = await Task.WhenAny(tcs.Task, Task.Delay(timeout));

                string stdout;
                string stderr;

                if (finished != tcs.Task)
                {
                    try { if (!proc.HasExited) proc.Kill(true); } catch (Exception killEx) { _logger.LogWarning(killEx, "Failed to kill python process after timeout"); }
                    // Give streams a moment to flush
                    await Task.Delay(200);
                    lock (stdoutSb) { stdout = stdoutSb.ToString(); }
                    lock (stderrSb) { stderr = stderrSb.ToString(); }
                    _logger.LogWarning("Python timed out. stdout: {stdout} stderr: {stderr}", stdout, stderr);
                    return Problem(detail: $"Python execution timed out after {timeout.TotalSeconds}s. stderr: {stderr}");
                }

                // Completed
                lock (stdoutSb) { stdout = stdoutSb.ToString(); }
                lock (stderrSb) { stderr = stderrSb.ToString(); }

                proc.Exited -= ExitedHandler;

                // Extract JSON object from stdout (robustly)
                string? json = null;
                if (!string.IsNullOrEmpty(stdout))
                {
                    // Prefer extracting the JSON block between the first '{' and the last '}' to handle multi-line output or surrounding noise
                    int firstBrace = stdout.IndexOf('{');
                    int lastBrace = stdout.LastIndexOf('}');
                    if (firstBrace >= 0 && lastBrace > firstBrace)
                    {
                        json = stdout.Substring(firstBrace, lastBrace - firstBrace + 1);
                    }
                    else
                    {
                        // Fallback to searching line-by-line for a single-line JSON object
                        var lines = stdout.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
                        for (int i = lines.Length - 1; i >= 0; i--)
                        {
                            var s = lines[i].Trim();
                            if (s.StartsWith('{') && s.EndsWith('}')) { json = s; break; }
                        }
                    }
                }

                if (json == null)
                {
                    _logger.LogWarning("Python did not return JSON. stdout: {stdout} stderr: {stderr}", stdout, stderr);
                    return Problem(detail: $"Python did not return JSON. stderr: {stderr}");
                }

                _logger.LogInformation("Python JSON candidate: {json}", json);

                // Try parse, with a tolerant fallback
                try
                {
                    var parsed = JsonDocument.Parse(json);
                    return new JsonResult(parsed.RootElement);
                }
                catch (JsonException)
                {
                    // fallback: replace single quotes with double quotes (best-effort)
                    var replaced = json.Replace("'", "\"");
                    try
                    {
                        var parsed = JsonDocument.Parse(replaced);
                        return new JsonResult(parsed.RootElement);
                    }
                    catch (JsonException parseEx)
                    {
                        _logger.LogError(parseEx, "Failed to parse python JSON candidate. stdout: {stdout} stderr: {stderr}", stdout, stderr);
                        return Problem(detail: $"Failed to parse Python JSON output. stderr: {stderr} stdout: {stdout}");
                    }
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Error running python script");
                return Problem(detail: ex.Message);
            }
        }
    }
}
