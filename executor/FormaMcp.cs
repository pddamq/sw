using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using DISPPARAMS = System.Runtime.InteropServices.ComTypes.DISPPARAMS;
using EXCEPINFO = System.Runtime.InteropServices.ComTypes.EXCEPINFO;
using System.Text;
using System.Web.Script.Serialization;

// .NET Framework 4.8 / C# 5. All COM work stays on the single STA main thread.
internal static class FormaMcp
{
    const string Version = "0.5.0";
    const int MaxLine = 1024 * 1024;
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024, RecursionLimit = 32 };
    static readonly Dictionary<string, Dictionary<string, object>> Tools = new Dictionary<string, Dictionary<string, object>>();
    static dynamic App;
    static object OwnedSketchDocument;
    static object OwnedSketchObject;
    static string OwnedSketchName;

    [ComImport, Guid("00020400-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface Dispatch
    {
        void GetTypeInfoCount(out uint count);
        void GetTypeInfo(uint index, uint locale, out IntPtr info);
        void GetIDsOfNames(ref Guid iid, [MarshalAs(UnmanagedType.LPArray, ArraySubType = UnmanagedType.LPWStr)] string[] names, uint count, uint locale, [Out] int[] ids);
        void Invoke(int member, ref Guid iid, uint locale, ushort flags, ref DISPPARAMS parameters,
            [MarshalAs(UnmanagedType.Struct)] out object result, ref EXCEPINFO exception, out uint argumentError);
    }
    static int DocumentType(object doc)
    {
        // Invoke IDispatch explicitly: C# dynamic GetType() can bind to System.Object.GetType().
        var dispatch = (Dispatch)doc;
        Guid empty = Guid.Empty;
        var ids = new int[1];
        dispatch.GetIDsOfNames(ref empty, new[] { "GetType" }, 1, 1033, ids);
        var parameters = new DISPPARAMS();
        var exception = new EXCEPINFO();
        object result;
        uint argumentError;
        dispatch.Invoke(ids[0], ref empty, 1033, 1, ref parameters, out result, ref exception, out argumentError);
        return Convert.ToInt32(result, CultureInfo.InvariantCulture);
    }

    static Dictionary<string, object> Map(params object[] items)
    {
        var result = new Dictionary<string, object>();
        for (int i = 0; i < items.Length; i += 2) result.Add((string)items[i], items[i + 1]);
        return result;
    }
    static Dictionary<string, object> Field(string type, string description)
    { return Map("type", type, "description", description); }
    static void Tool(string name, string description, Dictionary<string, object> properties, params string[] required)
    {
        Tools.Add(name, Map("name", name, "description", description,
            "inputSchema", Map("type", "object", "properties", properties, "required", required, "additionalProperties", false)));
    }
    static void Register()
    {
        Tool("sw_help", "Describe this built-in BASIC executor's capabilities and limits. No CAD operation.", Map());
        Tool("sw_status", "Read the current SOLIDWORKS attachment state. Never launches or attaches automatically.", Map());
        Tool("sw_connect", "Attach to a running SOLIDWORKS. launch=true explicitly starts SW only if attachment fails.", Map("launch", Field("boolean", "Default false; starting a new SW instance needs an explicit user request.")));
        Tool("doc_list", "List open SOLIDWORKS documents, at most 128.", Map());
        Tool("doc_new", "Create a PART using the configured SW default part template, or an explicit .prtdot template. Activates the new document.", Map("template_path", Field("string", "Optional absolute path to an existing .prtdot file.")));
        Tool("doc_open", "Open an existing native .sldprt, .sldasm or .slddrw document. Return SW errors and warnings.", Map("path", Field("string", "Absolute native document path.")), "path");
        Tool("doc_save", "Save the active document to a native SW file. Existing files require overwrite=true. This writes to disk; obtain user intent first.", Map("path", Field("string", "Absolute .sldprt/.sldasm/.slddrw path matching document type."), "overwrite", Field("boolean", "Default false. Explicitly allow an existing destination to be replaced.")), "path");
        Tool("model_info", "Read active document title, path, type and unsaved flag. This is not a design acceptance check.", Map());
        Tool("doc_rebuild", "Force rebuild the active document. Report the actual API error flag; not complete design verification.", Map());
        Tool("feature_list", "List top-level feature names and types, at most 500. Use exact RefPlane names for sketch_start.", Map());
        Tool("model_measure", "Read active PART mass properties in SI units. Uses current SW material/density; not proof of intended material or tolerances.", Map());
        Tool("sketch_start", "Start a new 2D sketch on an exact named reference plane in the active PART. Fails if another sketch is being edited.", Map("plane_name", Field("string", "Exact RefPlane feature name returned by feature_list; works with localized SW names.")), "plane_name");
        Tool("sketch_rectangle", "Add an axis-aligned corner rectangle to the sketch opened by this executor. Coordinates are local sketch millimeters. Not dimension-constrained.", Map("x1_mm", Field("number", "First corner X."), "y1_mm", Field("number", "First corner Y."), "x2_mm", Field("number", "Opposite corner X."), "y2_mm", Field("number", "Opposite corner Y.")), "x1_mm", "y1_mm", "x2_mm", "y2_mm");
        Tool("sketch_circle", "Add a circle to the sketch opened by this executor. Local sketch millimeters. Not dimension-constrained.", Map("x_mm", Field("number", "Center X."), "y_mm", Field("number", "Center Y."), "radius_mm", Field("number", "Positive radius.")), "x_mm", "y_mm", "radius_mm");
        Tool("sketch_finish", "Close only the sketch opened by this executor. Returns its exact feature name. Does not claim the sketch is fully constrained.", Map());
        Tool("feature_extrude", "Create a blind solid boss extrusion from an exact closed 2D sketch in the active PART. Depth in millimeters. No cut, draft or assembly support. A rebuild failure may leave a feature; inspect before retrying.", Map("sketch_name", Field("string", "Exact closed ProfileFeature name."), "depth_mm", Field("number", "Positive blind depth, at most 100000 mm."), "reverse", Field("boolean", "Reverse extrusion direction; default false.")), "sketch_name", "depth_mm");
        Tool("view_fit", "Zoom to fit the active document. Changes the visible viewport.", Map());
        Tool("view_screenshot", "Capture the current actual SW viewport as PNG, at most 1600px. Does not change orientation. Requires visible unlocked SW; image alone is not engineering acceptance.", Map());
    }
    static string Text(Dictionary<string, object> args, string name, bool optional)
    {
        object value;
        if (!args.TryGetValue(name, out value)) { if (optional) return ""; throw new ArgumentException("Missing argument: " + name); }
        var text = value as string;
        if (String.IsNullOrWhiteSpace(text) || text.Length > 4096) throw new ArgumentException("Expected a nonempty string: " + name);
        return text;
    }
    static bool Flag(Dictionary<string, object> args, string name)
    {
        object value;
        if (!args.TryGetValue(name, out value)) return false;
        if (!(value is bool)) throw new ArgumentException("Expected boolean: " + name);
        return (bool)value;
    }
    static double Number(Dictionary<string, object> args, string name)
    {
        object value;
        if (!args.TryGetValue(name, out value) || !(value is int || value is long || value is double || value is decimal))
            throw new ArgumentException("Expected number: " + name);
        double number = Convert.ToDouble(value, CultureInfo.InvariantCulture);
        if (Double.IsNaN(number) || Double.IsInfinity(number) || Math.Abs(number) > 100000)
            throw new ArgumentException("Coordinate/distance must be finite and within +/-100000 mm: " + name);
        return number / 1000.0;
    }
    static void Validate(string name, Dictionary<string, object> args)
    {
        var schema = (Dictionary<string, object>)Tools[name]["inputSchema"];
        var props = (Dictionary<string, object>)schema["properties"];
        foreach (string key in args.Keys) if (!props.ContainsKey(key)) throw new ArgumentException("Unknown argument: " + key);
        foreach (string key in (string[])schema["required"]) if (!args.ContainsKey(key)) throw new ArgumentException("Missing argument: " + key);
        foreach (string key in args.Keys)
        {
            string type = (string)((Dictionary<string, object>)props[key])["type"];
            if (type == "number") Number(args, key);
            else if (type == "boolean") Flag(args, key);
            else Text(args, key, false);
        }
    }
    static dynamic NeedApp()
    {
        if (App == null) throw new InvalidOperationException("SOLIDWORKS is not attached. Start installed SW, then call sw_connect(launch=false). No model operation was performed.");
        return App;
    }
    static dynamic Document(bool part)
    {
        dynamic doc = NeedApp().ActiveDoc;
        if (doc == null) throw new InvalidOperationException("No active SOLIDWORKS document.");
        if (part && DocumentType((object)doc) != 1) throw new InvalidOperationException("This tool requires an active PART document.");
        return doc;
    }
    static Dictionary<string, object> Info(dynamic doc)
    {
        return Map("title", (string)doc.GetTitle(), "path", (string)doc.GetPathName(), "document_type", DocumentType((object)doc), "unsaved", (bool)doc.GetSaveFlag());
    }
    static Dictionary<string, object> Status()
    {
        var info = Map("success", true, "attached", false, "executor", "forma-built-in-basic", "version", Version, "tool_count", Tools.Count,
            "cad_live_validated", false, "detail", "Executor is running. SOLIDWORKS is not attached; start installed SW then connect.");
        if (App != null)
        {
            try
            {
                info["revision"] = (string)App.RevisionNumber();
                info["attached"] = true;
                info["detail"] = "Attached to SOLIDWORKS. Task-specific geometry still requires verification.";
            }
            catch (Exception ex) { App = null; OwnedSketchDocument = null; info["detail"] = "Previous SW connection is unavailable: " + ex.Message; }
        }
        return info;
    }
    static string AbsolutePath(string value, string extension, bool existing)
    {
        if (value.Length < 3 || !Char.IsLetter(value[0]) || value[1] != ':' || (value[2] != '\\' && value[2] != '/') || value.IndexOf(':', 2) >= 0)
            throw new ArgumentException("Use an absolute local file path (no network paths or alternate streams).");
        string path = Path.GetFullPath(value);
        if (!String.Equals(Path.GetExtension(path), extension, StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("Expected file extension: " + extension);
        if (existing && !File.Exists(path)) throw new ArgumentException("File does not exist: " + path);
        return path;
    }
    static dynamic OwnedSketch()
    {
        dynamic doc = Document(true);
        if (OwnedSketchDocument == null || !Object.ReferenceEquals((object)doc, OwnedSketchDocument) || doc.SketchManager.ActiveSketch == null)
            throw new InvalidOperationException("No active sketch owned by this executor. Call sketch_start; user-created sketch edits are left untouched.");
        dynamic active = doc.SketchManager.ActiveSketch;
        if (!Object.ReferenceEquals((object)active, OwnedSketchObject)) throw new InvalidOperationException("The active sketch changed outside the executor; inspect the document before continuing.");
        return doc;
    }
    static object Execute(string name, Dictionary<string, object> args)
    {
        if (name == "sw_help") return Map("success", true, "executor", "forma-built-in-basic", "version", Version, "tool_count", Tools.Count,
            "units", "Sketch coordinates and extrusion depth: millimeters, internally converted to meters. Mass properties: SI.",
            "workflow", "sw_connect -> doc_new -> feature_list -> sketch_start(plane_name) -> sketch_rectangle/sketch_circle -> sketch_finish -> feature_extrude(sketch_name,depth_mm) -> doc_rebuild -> model_measure -> view_fit -> view_screenshot -> doc_save(path)",
            "limits", "18 basic tools only, not the upstream full toolset. No arbitrary C#/VBA, assembly editing, drawings creation, simulation, cuts, fillets, dimensions or constraints. New sketches remain unconstrained. COM API failures may leave partial changes; inspect before retrying. SW2023 live validation is outstanding.");
        if (name == "sw_status") return Status();
        if (name == "sw_connect")
        {
            try { App = Marshal.GetActiveObject("SldWorks.Application"); }
            catch (COMException)
            {
                if (!Flag(args, "launch")) throw new InvalidOperationException("No running registered SOLIDWORKS instance was found. Start SW with the same Windows user and privilege level, then reconnect. SW was not launched.");
                var type = Type.GetTypeFromProgID("SldWorks.Application");
                if (type == null) throw new InvalidOperationException("SOLIDWORKS COM is not registered. Install/repair SOLIDWORKS on this computer.");
                App = Activator.CreateInstance(type);
                App.Visible = true;
            }
            return Status();
        }
        if (name == "doc_list")
        {
            var documents = new List<object>();
            dynamic current = NeedApp().GetFirstDocument();
            while (current != null && documents.Count < 128) { documents.Add(Info(current)); current = current.GetNext(); }
            return Map("success", true, "documents", documents, "truncated", current != null);
        }
        if (name == "doc_new")
        {
            NeedApp();
            if (App.ActiveDoc != null && App.ActiveDoc.SketchManager.ActiveSketch != null) throw new InvalidOperationException("Finish the current sketch before creating a document.");
            string template = Text(args, "template_path", true);
            dynamic created = template.Length == 0 ? App.NewPart() : App.NewDocument(AbsolutePath(template, ".prtdot", true), 0, 0.0, 0.0);
            if (created == null) throw new InvalidOperationException("SW did not create a part. Configure a default part template in SW, or supply template_path.");
            return Map("success", true, "document", Info(created));
        }
        if (name == "doc_open")
        {
            string path = Text(args, "path", false);
            string ext = Path.GetExtension(path).ToLowerInvariant();
            int kind = ext == ".sldprt" ? 1 : ext == ".sldasm" ? 2 : ext == ".slddrw" ? 3 : 0;
            if (kind == 0) throw new ArgumentException("Only native .sldprt/.sldasm/.slddrw documents are supported.");
            path = AbsolutePath(path, ext, true);
            int errors = 0, warnings = 0;
            dynamic opened = NeedApp().OpenDoc6(path, kind, 1, "", ref errors, ref warnings);
            if (opened == null) throw new InvalidOperationException("SW open failed. error_code=" + errors + "; warning_code=" + warnings);
            return Map("success", errors == 0, "document", Info(opened), "error_code", errors, "warning_code", warnings);
        }
        dynamic doc = Document(name == "sketch_start" || name == "feature_extrude" || name == "model_measure");
        if (name == "model_info") return Map("success", true, "document", Info(doc));
        if (name == "doc_save")
        {
            if (doc.SketchManager.ActiveSketch != null) throw new InvalidOperationException("Finish the active sketch before saving.");
            int kind = DocumentType((object)doc);
            string ext = kind == 1 ? ".sldprt" : kind == 2 ? ".sldasm" : kind == 3 ? ".slddrw" : "";
            if (ext == "") throw new InvalidOperationException("Unsupported document type.");
            string path = AbsolutePath(Text(args, "path", false), ext, false);
            if (!Directory.Exists(Path.GetDirectoryName(path))) throw new ArgumentException("Destination directory must already exist.");
            if (File.Exists(path) && !Flag(args, "overwrite")) throw new ArgumentException("Destination already exists. Explicit overwrite=true is required.");
            doc.ClearSelection2(true);
            int errors = 0, warnings = 0;
            bool saved = doc.Extension.SaveAs(path, 0, 1, null, ref errors, ref warnings);
            return Map("success", saved && errors == 0 && File.Exists(path), "path", path, "error_code", errors, "warning_code", warnings);
        }
        if (name == "doc_rebuild")
        {
            if (doc.SketchManager.ActiveSketch != null) throw new InvalidOperationException("Finish the active sketch before rebuild.");
            bool rebuilt = doc.ForceRebuild3(false);
            return Map("success", rebuilt, "rebuilt", rebuilt, "note", "ForceRebuild3 reports whether rebuilding succeeded. This is not design acceptance.");
        }
        if (name == "feature_list")
        {
            var features = new List<object>();
            dynamic feature = doc.FirstFeature();
            while (feature != null && features.Count < 500)
            {
                features.Add(Map("name", (string)feature.Name, "type", (string)feature.GetTypeName2()));
                feature = feature.GetNextFeature();
            }
            return Map("success", true, "features", features, "truncated", feature != null);
        }
        if (name == "model_measure")
        {
            if (doc.SketchManager.ActiveSketch != null) throw new InvalidOperationException("Finish the active sketch before measuring.");
            doc.ClearSelection2(true);
            dynamic mass = doc.Extension.CreateMassProperty();
            if (mass == null) throw new InvalidOperationException("SW returned no mass properties. Check that a solid body exists and rebuild first.");
            mass.UseSystemUnits = true;
            return Map("success", true, "mass_kg", (double)mass.Mass, "volume_m3", (double)mass.Volume, "area_m2", (double)mass.SurfaceArea,
                "center_of_mass_m", (object)mass.CenterOfMass, "note", "Current SW density/material only; dimensions and tolerances require separate checks.");
        }
        if (name == "sketch_start")
        {
            if (doc.SketchManager.ActiveSketch != null) throw new InvalidOperationException("A sketch is already being edited; it was left untouched.");
            dynamic plane = doc.FeatureByName(Text(args, "plane_name", false));
            if (plane == null || (string)plane.GetTypeName2() != "RefPlane") throw new ArgumentException("No exact named RefPlane found. Read feature_list first.");
            doc.ClearSelection2(true);
            if (!(bool)plane.Select2(false, 0)) throw new InvalidOperationException("SW could not select the reference plane.");
            doc.SketchManager.InsertSketch(true);
            dynamic sketch = doc.SketchManager.ActiveSketch;
            if (sketch == null) throw new InvalidOperationException("SW did not start a sketch.");
            dynamic feature = doc.FirstFeature();
            string sketchName = null;
            for (int i = 0; feature != null && i < 500; ++i)
            {
                if ((string)feature.GetTypeName2() == "ProfileFeature" && Object.ReferenceEquals((object)feature.GetSpecificFeature2(), (object)sketch))
                { sketchName = (string)feature.Name; break; }
                feature = feature.GetNextFeature();
            }
            if (sketchName == null) throw new InvalidOperationException("A sketch was opened, but its feature identity could not be verified. Inspect SW and finish it manually before retrying.");
            OwnedSketchDocument = (object)doc;
            OwnedSketchObject = (object)sketch;
            OwnedSketchName = sketchName;
            return Map("success", true, "sketch_name", OwnedSketchName, "units", "local sketch mm", "fully_constrained", false);
        }
        if (name == "sketch_rectangle")
        {
            doc = OwnedSketch();
            double x1 = Number(args, "x1_mm"), y1 = Number(args, "y1_mm"), x2 = Number(args, "x2_mm"), y2 = Number(args, "y2_mm");
            if (Math.Abs(x2 - x1) < 1e-8 || Math.Abs(y2 - y1) < 1e-8) throw new ArgumentException("Rectangle width and height must be nonzero.");
            object created = doc.SketchManager.CreateCornerRectangle(x1, y1, 0.0, x2, y2, 0.0);
            if (created == null) throw new InvalidOperationException("SW returned no rectangle segments. Inspect the sketch before retrying.");
            return Map("success", true, "sketch_name", OwnedSketchName, "fully_constrained", false);
        }
        if (name == "sketch_circle")
        {
            doc = OwnedSketch();
            double radius = Number(args, "radius_mm");
            if (radius <= 0) throw new ArgumentException("radius_mm must be positive.");
            object created = doc.SketchManager.CreateCircleByRadius(Number(args, "x_mm"), Number(args, "y_mm"), 0.0, radius);
            if (created == null) throw new InvalidOperationException("SW returned no circle. Inspect the sketch before retrying.");
            return Map("success", true, "sketch_name", OwnedSketchName, "fully_constrained", false);
        }
        if (name == "sketch_finish")
        {
            doc = OwnedSketch();
            doc.SketchManager.InsertSketch(true);
            if (doc.SketchManager.ActiveSketch != null) throw new InvalidOperationException("SW did not close the sketch.");
            string finished = OwnedSketchName;
            OwnedSketchDocument = null; OwnedSketchObject = null; OwnedSketchName = null;
            return Map("success", true, "sketch_name", finished, "note", "Sketch closed; constraints and contour validity have not been verified.");
        }
        if (name == "feature_extrude")
        {
            double depth = Number(args, "depth_mm");
            if (depth <= 0) throw new ArgumentException("depth_mm must be positive.");
            if (doc.SketchManager.ActiveSketch != null) throw new InvalidOperationException("Call sketch_finish before extruding.");
            dynamic sketch = doc.FeatureByName(Text(args, "sketch_name", false));
            if (sketch == null || (string)sketch.GetTypeName2() != "ProfileFeature") throw new ArgumentException("No exact closed 2D ProfileFeature found.");
            doc.ClearSelection2(true);
            if (!(bool)sketch.Select2(false, 0)) throw new InvalidOperationException("SW could not select the sketch.");
            dynamic feature = doc.FeatureManager.FeatureExtrusion3(true, false, Flag(args, "reverse"), 0, 0, depth, 0.0,
                false, false, false, false, 0.0, 0.0, false, false, false, false, true, true, true, 0, 0.0, false);
            if (feature == null) throw new InvalidOperationException("SW returned no extrusion feature. Inspect contours and current feature tree before retrying.");
            bool rebuilt = doc.ForceRebuild3(false);
            return Map("success", rebuilt, "feature_name", (string)feature.Name, "rebuilt", rebuilt,
                "note", "Feature exists; verify resulting geometry, units and body count before accepting.");
        }
        if (name == "view_fit") { doc.ViewZoomtofit2(); doc.GraphicsRedraw2(); return Map("success", true); }
        if (name == "view_screenshot")
        {
            string temp = Path.Combine(Path.GetTempPath(), "forma-sw-" + Guid.NewGuid().ToString("N") + ".bmp");
            try
            {
                doc.GraphicsRedraw2();
                if (!(bool)doc.SaveBMP(temp, 0, 0) || !File.Exists(temp)) throw new InvalidOperationException("SW could not capture its viewport. Restore the SW window and unlock the desktop.");
                if (new FileInfo(temp).Length > 100 * 1024 * 1024) throw new InvalidOperationException("SW screenshot exceeds 100 MiB.");
                using (var source = Image.FromFile(temp))
                {
                    if ((long)source.Width * source.Height > 20000000) throw new InvalidOperationException("SW screenshot exceeds 20 million pixels.");
                    double scale = Math.Min(1.0, 1600.0 / Math.Max(source.Width, source.Height));
                    using (var image = new Bitmap(source, Math.Max(1, (int)(source.Width * scale)), Math.Max(1, (int)(source.Height * scale))))
                    using (var stream = new MemoryStream())
                    {
                        image.Save(stream, ImageFormat.Png);
                        if (stream.Length > 8 * 1024 * 1024) throw new InvalidOperationException("PNG exceeds 8 MiB.");
                        return Map("content", new object[] { Map("type", "text", "text", Json.Serialize(Map("success", true, "width", image.Width, "height", image.Height))),
                            Map("type", "image", "mimeType", "image/png", "data", Convert.ToBase64String(stream.ToArray())) });
                    }
                }
            }
            finally { try { File.Delete(temp); } catch (IOException) { } }
        }
        throw new ArgumentException("Unknown tool: " + name);
    }
    static void Send(object value) { Console.Out.WriteLine(Json.Serialize(value)); Console.Out.Flush(); }
    static void Error(object id, int code, string message) { Send(Map("jsonrpc", "2.0", "id", id, "error", Map("code", code, "message", message))); }
    static string ReadLine()
    {
        var line = new StringBuilder();
        int ch;
        while ((ch = Console.In.Read()) != -1)
        {
            if (ch == '\n') return line.ToString().TrimEnd('\r');
            if (line.Length == MaxLine) throw new InvalidDataException("MCP request exceeds 1 MiB; transport closed.");
            line.Append((char)ch);
        }
        return line.Length == 0 ? null : line.ToString();
    }
    static void Handle(string line)
    {
        Dictionary<string, object> request;
        try { request = Json.DeserializeObject(line) as Dictionary<string, object>; }
        catch (Exception) { Error(null, -32700, "Invalid JSON."); return; }
        if (request == null) { Error(null, -32600, "Expected JSON-RPC object."); return; }
        object id, method, version;
        bool hasId = request.TryGetValue("id", out id);
        if (!request.TryGetValue("jsonrpc", out version) || !Object.Equals(version, "2.0") || !request.TryGetValue("method", out method) || !(method is string))
        { Error(hasId ? id : null, -32600, "Invalid JSON-RPC request."); return; }
        // Notifications have no reply; cancellation never aborts a running COM mutation.
        if (!hasId) return;
        object raw;
        var args = request.TryGetValue("params", out raw) ? raw as Dictionary<string, object> : new Dictionary<string, object>();
        if (args == null) { Error(id, -32602, "params must be an object."); return; }
        object result;
        switch ((string)method)
        {
            case "initialize":
                object requested;
                string protocol = args.TryGetValue("protocolVersion", out requested) ? requested as string : "";
                if (protocol != "2024-11-05" && protocol != "2025-03-26" && protocol != "2025-06-18" && protocol != "2025-11-25") protocol = "2025-06-18";
                result = Map("protocolVersion", protocol, "capabilities", Map("tools", Map("listChanged", false)),
                    "serverInfo", Map("name", "forma-built-in-basic", "version", Version),
                    "instructions", "Basic executor only. Query sw_help for limits. SW2023 live validation outstanding. COM changes are serial and not automatically rolled back.");
                break;
            case "ping": result = Map(); break;
            case "tools/list": result = Map("tools", new List<Dictionary<string, object>>(Tools.Values)); break;
            case "tools/call":
                object toolValue, values;
                string name = args.TryGetValue("name", out toolValue) ? toolValue as string : null;
                if (name == null || !Tools.ContainsKey(name)) { Error(id, -32602, "Unknown tool."); return; }
                var inputs = args.TryGetValue("arguments", out values) ? values as Dictionary<string, object> : Map();
                if (inputs == null) { Error(id, -32602, "arguments must be an object."); return; }
                try
                {
                    Validate(name, inputs);
                    object output = Execute(name, inputs);
                    var blocks = output as Dictionary<string, object>;
                    result = blocks != null && blocks.ContainsKey("content") ? output : Map("isError", blocks != null && blocks.ContainsKey("success") && Object.Equals(blocks["success"], false),
                        "content", new object[] { Map("type", "text", "text", Json.Serialize(output)) });
                }
                catch (Exception ex)
                {
                    result = Map("isError", true, "content", new object[] { Map("type", "text", "text", Json.Serialize(Map("success", false,
                        "error", ex.Message, "hresult", ex.HResult, "note", "An API error may leave partial changes. Inspect current SW state before retrying."))) });
                }
                break;
            default: Error(id, -32601, "Method not found."); return;
        }
        Send(Map("jsonrpc", "2.0", "id", id, "result", result));
    }
    [STAThread]
    static int Main(string[] args)
    {
        Console.InputEncoding = new UTF8Encoding(false, true);
        Console.OutputEncoding = new UTF8Encoding(false);
        Register();
        if (args.Length == 1 && args[0] == "--version") { Console.WriteLine("Forma built-in basic MCP " + Version); return 0; }
        if (args.Length != 0) { Console.Error.WriteLine("Usage: forma-mcp.exe [--version]"); return 2; }
        try
        {
            string line;
            while ((line = ReadLine()) != null) if (line.Length != 0) Handle(line);
            return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex.Message); return 1; }
        finally { OwnedSketchDocument = null; App = null; /* Never close documents or quit the user's SW. */ }
    }
}
