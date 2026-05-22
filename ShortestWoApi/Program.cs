using System;
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.Hosting;

var builder = WebApplication.CreateBuilder(args);

// Add MVC controllers
builder.Services.AddControllers();

var app = builder.Build();

// Honor a single command-line URL argument like "localhost:7070"
if (args.Length > 0)
{
    var url = args[0];
    if (!url.StartsWith("http://", StringComparison.OrdinalIgnoreCase) && !url.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
    {
        url = "http://" + url;
    }
    app.Urls.Add(url);
}

// Host the UI under the path base that the frontend expects
app.UsePathBase("/shortestwopath");

// Serve static files from wwwroot
app.UseStaticFiles();

app.UseRouting();
app.MapControllers();

// Fall back to index.html for single-page UI
app.MapFallbackToFile("index.html");

app.Run();
