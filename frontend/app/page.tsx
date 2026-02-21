"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { io, Socket } from "socket.io-client";

// Types
interface RobotStatus {
  motor: string;
  motor_speed: number;
  pump: boolean;
  wifi_rssi: number;
  uptime: number;
  free_heap: number;
  mqtt_connected: boolean;
}

interface SensorDistance {
  distance: number;
  unit: string;
  timestamp: number;
}

interface SensorFlame {
  digital: boolean;
  analog: number;
  timestamp: number;
}

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000";
const ROBOT_TIMEOUT = 3000; // 3 seconds - if no status, mark offline

export default function DashboardPage() {
  // State
  const [socket, setSocket] = useState<Socket | null>(null);
  const [connected, setConnected] = useState(false);
  const [robotStatus, setRobotStatus] = useState<RobotStatus | null>(null);
  const [robotOnline, setRobotOnline] = useState(false);
  const [sensorDistance, setSensorDistance] = useState<SensorDistance | null>(
    null,
  );
  const [sensorFlame, setSensorFlame] = useState<SensorFlame | null>(null);
  const [keysPressed, setKeysPressed] = useState<Set<string>>(new Set());
  const [speed, setSpeed] = useState(200);
  const lastStatusTime = useRef<number>(0);

  // Connect to WebSocket
  useEffect(() => {
    const socketIo = io(BACKEND_URL, {
      transports: ["websocket"],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionAttempts: 10,
    });

    socketIo.on("connect", () => {
      console.log("[WS] Connected to backend");
      setConnected(true);
    });

    socketIo.on("disconnect", () => {
      console.log("[WS] Disconnected from backend");
      setConnected(false);
    });

    socketIo.on("robot_status", (data: RobotStatus) => {
      setRobotStatus(data);
      lastStatusTime.current = Date.now();
      setRobotOnline(true);
    });

    socketIo.on("sensor_distance", (data: SensorDistance) => {
      setSensorDistance(data);
      console.log("[Sensor] Distance:", data.distance, data.unit);
    });

    socketIo.on("sensor_flame", (data: SensorFlame) => {
      setSensorFlame(data);
      console.log(
        "[Sensor] Flame:",
        data.digital ? "DETECTED" : "No fire",
        "- Analog:",
        data.analog,
      );
    });

    socketIo.on("error", (error: any) => {
      console.error("[WS] Error:", error);
    });

    setSocket(socketIo);

    return () => {
      socketIo.disconnect();
    };
  }, []);

  // Check robot timeout (mark offline if no status for 3 seconds)
  useEffect(() => {
    const interval = setInterval(() => {
      const now = Date.now();
      const timeSinceLastStatus = now - lastStatusTime.current;

      if (timeSinceLastStatus > ROBOT_TIMEOUT && robotOnline) {
        console.log("[Robot] Timeout - marking offline");
        setRobotOnline(false);
      }
    }, 1000); // Check every second

    return () => clearInterval(interval);
  }, [robotOnline]);

  // Send motor command
  const sendMotorCommand = useCallback(
    (action: string, commandSpeed?: number) => {
      if (socket && connected) {
        const actualSpeed = commandSpeed !== undefined ? commandSpeed : speed;
        socket.emit("motor_command", { action, speed: actualSpeed });
        console.log(`[Motor] ${action} @ ${actualSpeed}`);
      }
    },
    [socket, connected, speed],
  );

  // Send pump command
  const sendPumpCommand = useCallback(
    (state: string) => {
      if (socket && connected) {
        socket.emit("pump_command", { state });
        console.log(`[Pump] ${state}`);
      }
    },
    [socket, connected],
  );

  // Keyboard control - Press and hold
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const key = e.key.toLowerCase();

      // Prevent default browser actions for control keys
      if (["w", "a", "s", "d", " ", "q", "e"].includes(key)) {
        e.preventDefault();
      }

      // Only send command if key not already pressed (avoid spam)
      if (!keysPressed.has(key)) {
        setKeysPressed((prev) => new Set(prev).add(key));

        // Motor commands (hold to move) - use current speed
        if (key === "w") sendMotorCommand("forward");
        else if (key === "s") sendMotorCommand("backward");
        else if (key === "a")
          sendMotorCommand("left", Math.floor(speed)); // Slightly slower for turns
        else if (key === "d")
          sendMotorCommand("right", Math.floor(speed));
        // Pump commands (toggle)
        else if (key === " ") sendPumpCommand("toggle");
        else if (key === "q") sendPumpCommand("on");
        else if (key === "e") sendPumpCommand("off");
      }
    };

    const handleKeyUp = (e: KeyboardEvent) => {
      const key = e.key.toLowerCase();

      setKeysPressed((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });

      // Stop motor when key released (WASD only)
      if (["w", "a", "s", "d"].includes(key)) {
        sendMotorCommand("stop", 0);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
    };
  }, [keysPressed, sendMotorCommand, sendPumpCommand, speed]);

  // Format uptime
  const formatUptime = (seconds: number) => {
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    return `${hrs}h ${mins}m ${secs}s`;
  };

  return (
    <div className="min-h-screen bg-gray-900 text-white p-8">
      {/* Header */}
      <div className="max-w-6xl mx-auto">
        <div className="flex items-center justify-between mb-8">
          <h1 className="text-3xl font-bold">Fire Fighting Robot Dashboard</h1>

          {/* Connection Status */}
          <div className="flex gap-4">
            <div className="flex items-center gap-2">
              <div
                className={`w-3 h-3 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`}
              />
              <span className="text-sm">
                Server: {connected ? "Connected" : "Disconnected"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <div
                className={`w-3 h-3 rounded-full ${robotOnline ? "bg-green-500" : "bg-red-500"}`}
              />
              <span className="text-sm">
                Robot: {robotOnline ? "Online" : "Offline"}
              </span>
            </div>
          </div>
        </div>

        {/* Main Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Control Panel */}
          <div className="bg-gray-800 rounded-lg p-6">
            <h2 className="text-xl font-semibold mb-4">Manual Control</h2>

            {/* Speed Control Slider */}
            <div className="mb-6 p-4 bg-gray-900 rounded">
              <div className="flex items-center justify-between mb-2">
                <label className="text-sm font-medium text-gray-400">
                  Speed Control
                </label>
                <span className="text-2xl font-bold text-blue-400">
                  {speed}
                </span>
              </div>
              <input
                type="range"
                min="0"
                max="255"
                step="5"
                value={speed}
                onChange={(e) => setSpeed(Number(e.target.value))}
                className="w-full h-3 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
              />
              <div className="flex justify-between text-xs text-gray-500 mt-1">
                <span>0 (Stop)</span>
                <span>128 (Medium)</span>
                <span>255 (Max)</span>
              </div>
            </div>

            {/* Keyboard Layout Visual */}
            <div className="mb-6">
              <div className="grid grid-cols-3 gap-2 max-w-xs mx-auto">
                <div className="col-start-2">
                  <button
                    className={`w-full h-16 rounded border-2 transition-all ${
                      keysPressed.has("w")
                        ? "bg-blue-600 border-blue-400 scale-95"
                        : "bg-gray-700 border-gray-600 hover:bg-gray-600"
                    }`}
                    onMouseDown={() => sendMotorCommand("forward")}
                    onMouseUp={() => sendMotorCommand("stop", 0)}
                    onMouseLeave={() => sendMotorCommand("stop", 0)}
                  >
                    <div className="text-xs">W</div>
                  </button>
                </div>

                <div className="col-start-1">
                  <button
                    className={`w-full h-16 rounded border-2 transition-all ${
                      keysPressed.has("a")
                        ? "bg-blue-600 border-blue-400 scale-95"
                        : "bg-gray-700 border-gray-600 hover:bg-gray-600"
                    }`}
                    onMouseDown={() =>
                      sendMotorCommand("left", Math.floor(speed))
                    }
                    onMouseUp={() => sendMotorCommand("stop", 0)}
                    onMouseLeave={() => sendMotorCommand("stop", 0)}
                  >
                    <div className="text-xs">A</div>
                  </button>
                </div>

                <button
                  className={`w-full h-16 rounded border-2 transition-all ${
                    keysPressed.has("s")
                      ? "bg-blue-600 border-blue-400 scale-95"
                      : "bg-gray-700 border-gray-600 hover:bg-gray-600"
                  }`}
                  onMouseDown={() => sendMotorCommand("backward")}
                  onMouseUp={() => sendMotorCommand("stop", 0)}
                  onMouseLeave={() => sendMotorCommand("stop", 0)}
                >
                  <div className="text-xs">S</div>
                </button>

                <button
                  className={`w-full h-16 rounded border-2 transition-all ${
                    keysPressed.has("d")
                      ? "bg-blue-600 border-blue-400 scale-95"
                      : "bg-gray-700 border-gray-600 hover:bg-gray-600"
                  }`}
                  onMouseDown={() =>
                    sendMotorCommand("right", Math.floor(speed ))
                  }
                  onMouseUp={() => sendMotorCommand("stop", 0)}
                  onMouseLeave={() => sendMotorCommand("stop", 0)}
                >
                  <div className="text-xs">D</div>
                </button>
              </div>
            </div>

            {/* Pump Control */}
            <div className="space-y-3">
              <h3 className="text-sm font-medium text-gray-400">
                Pump Control
              </h3>
              <div className="grid grid-cols-3 gap-2">
                <button
                  onClick={() => sendPumpCommand("on")}
                  className={`py-3 px-4 rounded transition-all ${
                    robotStatus?.pump
                      ? "bg-green-600 hover:bg-green-700"
                      : "bg-gray-700 hover:bg-gray-600"
                  }`}
                >
                  <div className="text-sm font-medium">ON</div>
                  <div className="text-xs text-gray-300">Q</div>
                </button>

                <button
                  onClick={() => sendPumpCommand("toggle")}
                  className="py-3 px-4 rounded bg-gray-700 hover:bg-gray-600 transition-all"
                >
                  <div className="text-sm font-medium">TOGGLE</div>
                  <div className="text-xs text-gray-300">SPACE</div>
                </button>

                <button
                  onClick={() => sendPumpCommand("off")}
                  className={`py-3 px-4 rounded transition-all ${
                    !robotStatus?.pump
                      ? "bg-red-600 hover:bg-red-700"
                      : "bg-gray-700 hover:bg-gray-600"
                  }`}
                >
                  <div className="text-sm font-medium">OFF</div>
                  <div className="text-xs text-gray-300">E</div>
                </button>
              </div>
            </div>
          </div>

          {/* Robot Status Panel */}
          <div className="bg-gray-800 rounded-lg p-6">
            <h2 className="text-xl font-semibold mb-4">Robot Status</h2>

            {robotStatus ? (
              <div className="space-y-4">
                {/* Motor Status */}
                <div className="bg-gray-900 rounded p-4">
                  <div className="text-sm text-gray-400 mb-1">Motor State</div>
                  <div className="flex items-center justify-between">
                    <span className="text-2xl font-bold capitalize">
                      {robotStatus.motor}
                    </span>
                    <span className="text-xl text-gray-400">
                      Speed: {robotStatus.motor_speed}
                    </span>
                  </div>
                </div>

                {/* Pump Status */}
                <div className="bg-gray-900 rounded p-4">
                  <div className="text-sm text-gray-400 mb-1">Pump State</div>
                  <div className="flex items-center gap-3">
                    <div
                      className={`w-4 h-4 rounded-full ${robotStatus.pump ? "bg-green-500" : "bg-gray-600"}`}
                    />
                    <span className="text-2xl font-bold">
                      {robotStatus.pump ? "ON" : "OFF"}
                    </span>
                  </div>
                </div>

                {/* WiFi Signal */}
                <div className="bg-gray-900 rounded p-4">
                  <div className="text-sm text-gray-400 mb-1">WiFi Signal</div>
                  <div className="flex items-center justify-between">
                    <span className="text-xl">{robotStatus.wifi_rssi} dBm</span>
                    <div className="flex gap-1">
                      {[...Array(4)].map((_, i) => (
                        <div
                          key={i}
                          className={`w-2 ${
                            robotStatus.wifi_rssi >= -50 - i * 15
                              ? "bg-green-500"
                              : "bg-gray-600"
                          }`}
                          style={{ height: `${8 + i * 4}px` }}
                        />
                      ))}
                    </div>
                  </div>
                </div>

                {/* System Info */}
                <div className="bg-gray-900 rounded p-4 space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-gray-400">Uptime:</span>
                    <span className="font-mono">
                      {formatUptime(robotStatus.uptime)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-400">Free Heap:</span>
                    <span className="font-mono">
                      {(robotStatus.free_heap / 1024).toFixed(1)} KB
                    </span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-center h-64 text-gray-500">
                Waiting for robot data...
              </div>
            )}
          </div>

          {/* Sensor Data Panel */}
          <div className="bg-gray-800 rounded-lg p-6">
            <h2 className="text-xl font-semibold mb-4">Sensor Data</h2>

            <div className="space-y-4">
              {/* Distance Sensor */}
              <div className="bg-gray-900 rounded p-4">
                <div className="text-sm text-gray-400 mb-3">
                  Distance (HC-SR04)
                </div>
                {sensorDistance ? (
                  <div className="flex items-end gap-2">
                    <span className="text-3xl font-bold">
                      {sensorDistance.distance.toFixed(1)}
                    </span>
                    <span className="text-lg text-gray-400 mb-1">
                      {sensorDistance.unit}
                    </span>
                  </div>
                ) : (
                  <div className="text-gray-500 text-sm py-2">No data</div>
                )}
              </div>

              {/* Flame Sensor */}
              <div className="bg-gray-900 rounded p-4">
                <div className="text-sm text-gray-400 mb-3">Flame Detector</div>
                {sensorFlame ? (
                  <div className="space-y-3">
                    <div className="flex justify-between items-center">
                      <span className="text-sm text-gray-400">Digital:</span>
                      <span className="text-lg font-semibold">
                        {sensorFlame.digital ? "Detected" : "No Fire"}
                      </span>
                    </div>

                    <div className="flex justify-between items-center">
                      <span className="text-sm text-gray-400">Analog:</span>
                      <span className="text-lg font-mono">
                        {sensorFlame.analog}
                      </span>
                    </div>
                  </div>
                ) : (
                  <div className="text-gray-500 text-sm py-2">No data</div>
                )}
              </div>

              {/* Sensor Status */}
              <div className="bg-gray-900 rounded p-3 text-xs space-y-1">
                <div className="flex justify-between text-gray-400">
                  <span>Distance:</span>
                  <span
                    className={
                      sensorDistance ? "text-green-400" : "text-gray-500"
                    }
                  >
                    {sensorDistance ? "Active" : "Offline"}
                  </span>
                </div>
                <div className="flex justify-between text-gray-400">
                  <span>Flame:</span>
                  <span
                    className={sensorFlame ? "text-green-400" : "text-gray-500"}
                  >
                    {sensorFlame ? "Active" : "Offline"}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Camera Stream Section */}
        <div className="mt-6">
          <h2 className="text-2xl font-semibold mb-4">Camera Streams</h2>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Original Stream */}
            <div className="bg-gray-800 rounded-lg p-6">
              <h3 className="text-lg font-semibold mb-4 text-gray-300">
                Original Feed
              </h3>
              <div className="bg-black rounded overflow-hidden aspect-video">
                <img
                  src={`${BACKEND_URL}/api/camera/stream/original`}
                  alt="Original Camera Feed"
                  className="w-full h-full object-contain"
                  onError={(e) => {
                    e.currentTarget.src = "";
                    e.currentTarget.alt = "Camera offline";
                  }}
                />
              </div>
              <div className="mt-3 text-sm text-gray-400 text-center">
                ESP32-CAM Raw Stream
              </div>
            </div>

            {/* AI Processed Stream */}
            <div className="bg-gray-800 rounded-lg p-6">
              <h3 className="text-lg font-semibold mb-4 text-gray-300">
                AI Detection
              </h3>
              <div className="bg-black rounded overflow-hidden aspect-video">
                <img
                  src={`${BACKEND_URL}/api/camera/stream/processed`}
                  alt="AI Processed Feed"
                  className="w-full h-full object-contain"
                  onError={(e) => {
                    e.currentTarget.src = "";
                    e.currentTarget.alt = "Camera offline";
                  }}
                />
              </div>
              <div className="mt-3 text-sm text-gray-400 text-center">
                Fire Detection (Future: YOLO Bounding Boxes)
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
