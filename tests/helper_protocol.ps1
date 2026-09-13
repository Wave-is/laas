# Protocol tests run the compiled handler as the current user on random test pipes.
# They never install/start a Windows service and never send a valid driver-change plan.
$ErrorActionPreference='Stop'
$stationAssemblyPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'src\services\LocalAgentGpuModeHelper.exe'
Add-Type -AssemblyName System.ServiceProcess
Add-Type -AssemblyName System.Web.Extensions
Add-Type -ReferencedAssemblies System.Core,System.ServiceProcess,System.Web.Extensions -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Pipes;
using System.Reflection;
using System.Security.Principal;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
public static class StationProtocolTest {
    public static void Run(string assemblyPath) {
        var type = Assembly.LoadFrom(assemblyPath).GetType("LocalAgentGpuModeHelper");
        var helper = Activator.CreateInstance(type, new object[]{ WindowsIdentity.GetCurrent().User.Value });
        var handle = type.GetMethod("Handle", BindingFlags.NonPublic|BindingFlags.Instance);
        var id = "GPU-00000000-0000-0000-0000-000000000000";
        var entry = "{\"gpu_stable_id\":\""+id+"\",\"target_mode\":\"TCC\"}";
        var tests = new string[] {
            "{}", "{\"Action\":5}", "{\"Action\":\"Unknown\"}",
            "{\"Action\":\"GetDriverModes\",\"Command\":\"whoami\"}",
            "{\"Action\":\"ApplyDriverModePlan\",\"Plan\":\"bad\"}",
            "{\"Action\":\"ApplyDriverModePlan\",\"Plan\":[{\"gpu_stable_id\":\"GPU-x\",\"target_mode\":\"TCC\"}]}",
            "{\"Action\":\"ApplyDriverModePlan\",\"Plan\":[{\"gpu_stable_id\":\""+id+"\",\"target_mode\":\"TCC;whoami\"}]}",
            "{\"Action\":\"ApplyDriverModePlan\",\"Plan\":["+entry+","+entry+"]}",
            new string('x', 33000), null
        };
        var json = new JavaScriptSerializer();
        for(int index=0; index<tests.Length; index++) {
            string name="StationProtocolTest-"+Guid.NewGuid().ToString("N");
            using(var server=new NamedPipeServerStream(name,PipeDirection.InOut,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous,65536,65536))
            using(var client=new NamedPipeClientStream(".",name,PipeDirection.InOut,PipeOptions.Asynchronous)) {
                var work=Task.Run(()=>{server.WaitForConnection();handle.Invoke(helper,new object[]{server});});
                client.Connect(3000);
                if(tests[index]!=null) {
                    byte[] bytes=Encoding.UTF8.GetBytes(tests[index]+"\n");
                    client.Write(bytes,0,bytes.Length);client.Flush();
                }
                var read=Task.Run(()=>new StreamReader(client).ReadLine());
                if(!read.Wait(8000)) throw new Exception("Protocol timeout in test "+index);
                var response=json.Deserialize<Dictionary<string,object>>(read.Result);
                if((bool)response["Success"]) throw new Exception("Invalid request accepted in test "+index);
                if(!work.Wait(3000)) throw new Exception("Handler did not finish");
            }
        }
        Console.WriteLine("10 compiled helper protocol tests passed; no service installation or GPU changes.");
    }
}
"@
[StationProtocolTest]::Run($stationAssemblyPath)
