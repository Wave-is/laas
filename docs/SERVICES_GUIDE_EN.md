SERVICES IN STATION: GETTING STARTED

This section stores connections to ComfyUI, image workers and other services.
One service can be used with different agents. Choosing an agent does not start
or stop it automatically.

THE SERVICE ALREADY RUNS ON ANOTHER COMPUTER

1. Click "Add service" and give it a clear name.
2. Choose "ComfyUI" for its own web interface or "Image worker / HTTP service"
   for a separate server with its own API.
3. Choose "Already running / another computer" and paste the address with the port.
4. Click "Save". If the computer is busy, leave automatic checks turned off.
5. When it is fine to check, click "Check" on the card. This is a single
   availability check: no images are queued and no models are loaded.
6. Click "Open address" to go to the service, or "Copy address"
   to pass it to your agent's generation tool.

Example of ComfyUI used directly:
Name: ComfyUI on the second PC
Type: ComfyUI
Address: http://192.168.1.10:8188
Station uses /system_stats for the check on its own. Replace the example with your address.

Example of a separate image worker:
Name: Image server
Type: Image worker / HTTP service
Address: http://192.168.1.10:8000
Check address: http://192.168.1.10:8000/health
Port 8000 and the /health path are examples. Take them from your server's settings.
An image worker may provide only an API, without a generation page in the browser.

A remote card has no commands to start or stop the process. They are managed
on the computer where the service is installed. "Remove from Station" deletes only
the saved connection; the server and its files stay in place.

HOW TO GET AN IMAGE

In the ComfyUI interface, open your working workflow, choose a model installed
there, enter a description and queue the workflow. For example:
"A small wooden house by a lake, morning fog, soft light".
The result appears in ComfyUI. The connection in Station is responsible for the address
and availability; workflows and models are stored on the ComfyUI server.

To generate from Qwen, Hermes, Pi or another agent, the agent needs its own tool or
an MCP connection to ComfyUI / the image worker. Enter the copied address there.
Registering a card does not by itself give the agent such a tool.

RUNNING ON THIS COMPUTER

1. Install the service itself and make sure you know its program and parameters.
2. In the form, choose "Run on this computer".
3. Choose the program file. For Python, this is python.exe from the service's environment.
4. Specify the working folder and parameters: each argument on a separate line,
   without adding quotes, even if the path contains spaces.
5. Save. Click "Start" on the card, then "Check".
6. To finish, click "Stop". Station ends only its own process.
   If the service is already running separately, add it as "Already running".

Example parameters for an installed copy of ComfyUI:
C:\ComfyUI\main.py
--listen
127.0.0.1

Replace the paths with real ones. They may differ in portable builds. The log appears
after a start through Station and opens with the "Log" button on the card.
Change the settings of a running local card or remove it only after stopping it.

WHAT THE STATUS MEANS

"Checks off": Station does not poll the address automatically. You can run
a single manual check. Its time and result are shown on the card.
"Available": the server answered the check; this is not a promise of successful generation.
"No connection": the server did not answer. Check the network/VPN, address and port; it may
be busy for a while. This is not a command to restart the other computer.
"Needs attention": the server answered with an error or returned a different API instead of ComfyUI.
"Process running": the local process exists, HTTP readiness is not confirmed yet.
"Stopped": Station does not see a local process that belongs to it.

To change the address, name or automatic checks, click "Configure" on the card. A new connection
is saved without automatic checks by default. When enabled, the interval is 30 seconds.

ComfyUI API description: https://docs.comfy.org/development/comfyui-server/comms_routes
