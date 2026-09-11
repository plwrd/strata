# WebView2 loader

`WebView2Loader.dll` — the shim that finds and starts the installed Microsoft Edge
WebView2 runtime. Strata ships **only this file** (~166 KB). The runtime itself is
not bundled and is never installed by us: it is present by default on Windows 11
and on most Windows 10 machines, and when it is absent the research pane falls
back to Qt WebEngine and says so.

See [ADR-0012](../../docs/adr/0012-webview2-browser-pane.md) for why the pane uses
WebView2 at all.

## Where it came from

The `Microsoft.Web.WebView2` NuGet package, `build/native/x64/WebView2Loader.dll`.
The same package carries the headers the COM binding is generated from.

## Updating it

```powershell
# 1. fetch the package (any version; the loader is forward-compatible)
curl -L -o webview2.zip https://www.nuget.org/api/v2/package/Microsoft.Web.WebView2/1.0.2903.40
Expand-Archive webview2.zip -DestinationPath webview2-sdk

# 2. replace the loader
Copy-Item webview2-sdk/build/native/x64/WebView2Loader.dll packaging/webview2/

# 3. regenerate the vtable constants from the matching headers
python -m scripts.webview2_slots webview2-sdk/build/native/include/WebView2.h
```

Step 3 is not optional when the SDK version changes. The binding calls WebView2 by
vtable slot, and a slot number that no longer matches the interface does not raise —
it calls whatever now sits at that offset. `scripts/webview2_slots.py` explains the
reasoning; `tests/unit/test_webview2_binding.py` covers the parts that can be tested
without a live runtime.

## Licence

`WebView2Loader.dll` is redistributable under the Microsoft Edge WebView2 Runtime
distribution terms. It is a Microsoft binary, unmodified.
