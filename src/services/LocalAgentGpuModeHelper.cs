// Build with the Windows .NET Framework C# compiler. No external dependencies.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using TimeoutException = System.TimeoutException;

public sealed class LocalAgentGpuModeHelper : ServiceBase {
    const string NameValue = "LocalAgentGpuModeHelper";
    readonly string userSid;
    readonly string smi = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "nvidia-smi.exe");
    volatile bool stopping;
    volatile bool cancelled;
    int switching;
    NamedPipeServerStream listener;
    readonly SemaphoreSlim clients = new SemaphoreSlim(8);
    public LocalAgentGpuModeHelper(string sid) { userSid = sid; ServiceName = NameValue; CanStop = true; }
    public static int Main(string[] args) {
        // Fallback without the service: Station starts this EXE once with a UAC prompt.
        if (args.Length == 4 && args[0] == "--apply-plan" && args[2] == "--result")
            return ApplyOnce(args[1], args[3]);
        if (args.Length != 2 || args[0] != "--user-sid" || !Regex.IsMatch(args[1], @"^S-1-5-21-(\d+-){3}\d+$"))
            throw new ArgumentException("A local installation user SID is required");
        ServiceBase.Run(new LocalAgentGpuModeHelper(args[1]));
        return 0;
    }
    static int ApplyOnce(string planPath, string resultPath) {
        var json = new JavaScriptSerializer { MaxJsonLength = 65536, RecursionLimit = 8 };
        object response;
        try {
            if (!new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator))
                throw new UnauthorizedAccessException("Administrator rights are required");
            var info = new FileInfo(planPath);
            if (!info.Exists || info.Length > 32768 || (info.Attributes & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException("Invalid plan file");
            var request = json.DeserializeObject(File.ReadAllText(planPath, Encoding.UTF8)) as Dictionary<string, object>;
            if (request == null || request.Count != 1 || !request.ContainsKey("Plan"))
                throw new InvalidDataException("Expected a typed Plan");
            response = new LocalAgentGpuModeHelper("S-1-5-18").Apply(request);
        } catch (Exception ex) { response = new { Success = false, Message = ex.GetBaseException().Message }; }
        // CreateNew never follows or overwrites an existing file or link planted at the result path.
        var directory = new DirectoryInfo(Path.GetDirectoryName(Path.GetFullPath(resultPath)));
        if ((directory.Attributes & FileAttributes.ReparsePoint) != 0) return 2;
        using (var stream = new FileStream(resultPath, FileMode.CreateNew, FileAccess.Write))
        using (var writer = new StreamWriter(stream, new UTF8Encoding(false)))
            writer.Write(json.Serialize(response));
        return 0;
    }
    protected override void OnStart(string[] args) {
        stopping = false;
        Task.Run((Action)Listen);
    }
    protected override void OnStop() {
        stopping = true; cancelled = true;
        if (listener != null) listener.Dispose();
    }
    PipeSecurity Security() {
        var acl = new PipeSecurity();
        acl.SetAccessRuleProtection(true, false);
        // Explicitly deny SMB/network logons even if they hold the allowed user SID.
        acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid, null), PipeAccessRights.FullControl, AccessControlType.Deny));
        acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(userSid), PipeAccessRights.ReadWrite, AccessControlType.Allow));
        acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), PipeAccessRights.FullControl, AccessControlType.Allow));
        acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null), PipeAccessRights.FullControl, AccessControlType.Allow));
        return acl;
    }
    void Listen() {
        while (!stopping) {
            try {
                clients.Wait();
                listener = new NamedPipeServerStream(NameValue, PipeDirection.InOut, 8,
                    PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 65536, 65536, Security());
                listener.WaitForConnection();
                var pipe = listener;
                Task.Run(() => { try { Handle(pipe); } finally { pipe.Dispose(); clients.Release(); } });
            } catch { if (!stopping) { clients.Release(); Thread.Sleep(250); } }
        }
    }
    void Handle(NamedPipeServerStream pipe) {
        var json = new JavaScriptSerializer { MaxJsonLength = 65536, RecursionLimit = 8 };
        object response;
        try {
            var read = Task.Run(() => {
                var bytes = new List<byte>();
                int value;
                while ((value = pipe.ReadByte()) >= 0 && value != 10) {
                    bytes.Add((byte)value);
                    if (bytes.Count > 32768) throw new InvalidDataException("Request too large");
                }
                return Encoding.UTF8.GetString(bytes.ToArray());
            });
            if (!read.Wait(3000)) throw new TimeoutException("Request timeout");
            var request = json.DeserializeObject(read.Result) as Dictionary<string, object>;
            if (request == null || !request.ContainsKey("Action") || !(request["Action"] is string))
                throw new InvalidDataException("Expected typed request");
            var action = (string)request["Action"];
            if (request.Keys.Any(k => k != "Action" && !(action == "ApplyDriverModePlan" && k == "Plan")))
                throw new InvalidDataException("Unknown request field");
            switch (action) {
                case "GetHardwareStatus":
                case "GetDriverModes":
                    var devices = Snapshot();
                    response = new { Success = true, Devices = devices,
                        Modes = devices.ToDictionary(d => d.Key, d => d.Value.Current) };
                    break;
                case "ApplyDriverModePlan":
                    response = Apply(request); break;
                case "CancelSwitch":
                    cancelled = true;
                    response = new { Success = true, Message = "Cancellation requested; in-flight driver operation must finish." }; break;
                default: throw new InvalidDataException("Unsupported action");
            }
        } catch (Exception ex) { response = new { Success = false, Message = ex.GetBaseException().Message }; }
        try {
            var bytes = Encoding.UTF8.GetBytes(json.Serialize(response) + "\n");
            var write = pipe.WriteAsync(bytes, 0, bytes.Length);
            if (!write.Wait(3000)) pipe.Dispose();
        } catch { }
    }
    sealed class Gpu {
        public string Current { get; set; }
        public string Pending { get; set; }
        public bool? DisplayActive { get; set; }
    }
    string RunSmi(string arguments) {
        var info = new ProcessStartInfo(smi, arguments) { UseShellExecute = false,
            CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
            WorkingDirectory = Path.GetDirectoryName(smi) };
        using (var p = Process.Start(info)) {
            var stdout = p.StandardOutput.ReadToEndAsync();
            var stderr = p.StandardError.ReadToEndAsync();
            if (!p.WaitForExit(10000)) { p.Kill(); throw new TimeoutException("nvidia-smi timeout"); }
            Task.WaitAll(stdout, stderr);
            if (p.ExitCode != 0) throw new InvalidOperationException("nvidia-smi: " + stderr.Result + stdout.Result);
            return stdout.Result;
        }
    }
    Dictionary<string, Gpu> Snapshot() {
        var result = new Dictionary<string, Gpu>(StringComparer.OrdinalIgnoreCase);
        foreach (var row in RunSmi("--query-gpu=uuid,driver_model.current,driver_model.pending,display_active --format=csv,noheader,nounits").Split('\n')) {
            var p = row.Split(',').Select(v => v.Trim()).ToArray();
            if (p.Length != 4 || !p[0].StartsWith("GPU-")) continue;
            result[p[0]] = new Gpu { Current = p[1].ToUpperInvariant(), Pending = p[2].ToUpperInvariant(),
                DisplayActive = p[3] == "Enabled" ? (bool?)true : p[3] == "Disabled" ? (bool?)false : null };
        }
        if (result.Count == 0) throw new InvalidOperationException("No NVIDIA devices discovered");
        return result;
    }
    object Apply(Dictionary<string, object> request) {
        if (!request.ContainsKey("Plan") || !(request["Plan"] is object[]))
            throw new InvalidDataException("Expected a typed Plan array");
        var entries = (object[])request["Plan"];
        if (entries.Length > 64) throw new InvalidDataException("Plan is too large");
        var plan = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var value in entries) {
            var entry = value as Dictionary<string, object>;
            if (entry == null || entry.Count != 2 || !entry.ContainsKey("gpu_stable_id") || !entry.ContainsKey("target_mode"))
                throw new InvalidDataException("Invalid plan entry");
            var id = entry["gpu_stable_id"] as string;
            var mode = entry["target_mode"] as string;
            if (id == null || !Regex.IsMatch(id, @"^GPU-[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$") ||
                (mode != "TCC" && mode != "WDDM" && mode != "UNCHANGED") || plan.ContainsKey(id))
                throw new InvalidDataException("Invalid UUID, duplicate GPU or mode");
            plan.Add(id, mode);
        }
        if (Interlocked.CompareExchange(ref switching, 1, 0) != 0)
            throw new InvalidOperationException("A GPU switch is already running");
        try {
            cancelled = false;
            if (!VerifyTrust(smi)) throw new InvalidOperationException("nvidia-smi Authenticode validation failed");
            var before = Snapshot();
            foreach (var entry in plan) {
                if (!before.ContainsKey(entry.Key)) throw new InvalidDataException("Unknown GPU UUID");
                if (entry.Value == "TCC" && before[entry.Key].Current != "TCC" && before[entry.Key].DisplayActive != false)
                    throw new InvalidOperationException("Refusing TCC on a display-active GPU or unknown display state");
            }
            var changed = new List<string>();
            foreach (var entry in plan) {
                if (cancelled) return new { Success = false, Cancelled = true, Changed = changed, Message = "Switch cancelled; refresh hardware status." };
                if (entry.Value == "UNCHANGED" || (before[entry.Key].Current == entry.Value && before[entry.Key].Pending == entry.Value)) continue;
                // Exact UUID and enum are validated above; there is no executable/arguments API.
                RunSmi("-i " + entry.Key + " -dm " + (entry.Value == "TCC" ? "1" : "0"));
                changed.Add(entry.Key);
            }
            var deadline = DateTime.UtcNow.AddSeconds(30);
            Dictionary<string, Gpu> after = null;
            do {
                try {
                    after = Snapshot();
                    if (plan.All(e => e.Value == "UNCHANGED" || (after.ContainsKey(e.Key) && after[e.Key].Current == e.Value && after[e.Key].Pending == e.Value)))
                        return new { Success = true, ChangedCount = changed.Count, Devices = after, Message = "Driver modes verified" };
                } catch { }
                Thread.Sleep(500);
            } while (DateTime.UtcNow < deadline && !cancelled);
            if (after != null && plan.All(e => e.Value == "UNCHANGED" || (after.ContainsKey(e.Key) && after[e.Key].Pending == e.Value)))
                return new { Success = false, RebootRequired = true, ChangedCount = changed.Count, Devices = after, Message = "Reboot Required: only pending modes match" };
            return new { Success = false, ChangedCount = changed.Count, Devices = after, Message = "Driver state could not be verified; inspect hardware before retrying" };
        } finally { Interlocked.Exchange(ref switching, 0); }
    }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct TrustFile { public uint Size; public string Path; public IntPtr File; public IntPtr KnownSubject; }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct TrustData {
        public uint Size; public IntPtr Policy; public IntPtr SIP; public uint UI; public uint Revocation;
        public uint Choice; public IntPtr File; public uint StateAction; public IntPtr State;
        public IntPtr Url; public uint Flags; public uint Context;
    }
    [DllImport("wintrust.dll", ExactSpelling = true, CharSet = CharSet.Unicode)]
    static extern int WinVerifyTrust(IntPtr hwnd, ref Guid action, ref TrustData data);
    static bool VerifyTrust(string path) {
        var file = new TrustFile { Size = (uint)Marshal.SizeOf(typeof(TrustFile)), Path = path };
        var pointer = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(TrustFile)));
        try {
            Marshal.StructureToPtr(file, pointer, false);
            var data = new TrustData { Size = (uint)Marshal.SizeOf(typeof(TrustData)), UI = 2, Choice = 1,
                File = pointer, StateAction = 1, Flags = 0x1000 };
            var action = new Guid("00AAC56B-CD44-11d0-8CC2-00C04FC295EE");
            int status = WinVerifyTrust(new IntPtr(-1), ref action, ref data);
            data.StateAction = 2;
            WinVerifyTrust(new IntPtr(-1), ref action, ref data);
            return status == 0;
        } finally { Marshal.DestroyStructure(pointer, typeof(TrustFile)); Marshal.FreeHGlobal(pointer); }
    }
}
