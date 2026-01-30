# Flutter Integration Guide - Real-time Audio Transcription API

This guide explains how to integrate the real-time audio transcription WebSocket API into your Flutter application.

## Overview

The API provides a WebSocket endpoint at `ws://localhost:8000/ws` (or your server URL) that accepts streaming audio and returns real-time transcriptions.

## Audio Format Requirements

- **Format**: 16-bit PCM (Pulse Code Modulation)
- **Channels**: Mono (1 channel)
- **Sample Rate**: 16,000 Hz (16 kHz)
- **Data Type**: Raw audio bytes

## Dependencies

Add these to your `pubspec.yaml`:

```yaml
dependencies:
  flutter:
    sdk: flutter
  web_socket_channel: ^2.4.0
  record: ^5.0.4  # For audio recording
  permission_handler: ^11.0.0  # For microphone permissions
```

## Basic Implementation

### 1. WebSocket Connection Setup

```dart
import 'package:web_socket_channel/web_socket_channel.dart';
import 'dart:convert';
import 'dart:typed_data';

class TranscriptionService {
  WebSocketChannel? _channel;
  final String serverUrl; // e.g., "ws://localhost:8000/ws"
  
  TranscriptionService({required this.serverUrl});
  
  // Connect to WebSocket
  Future<void> connect() async {
    try {
      _channel = WebSocketChannel.connect(Uri.parse(serverUrl));
      
      // Listen for messages
      _channel!.stream.listen(
        (message) {
          _handleMessage(message);
        },
        onError: (error) {
          print('WebSocket error: $error');
        },
        onDone: () {
          print('WebSocket connection closed');
        },
      );
    } catch (e) {
      print('Failed to connect: $e');
    }
  }
  
  // Handle incoming messages
  void _handleMessage(dynamic message) {
    try {
      final data = json.decode(message);
      final type = data['type'];
      
      switch (type) {
        case 'connected':
          print('Connected: ${data['message']}');
          break;
          
        case 'transcription':
          // Handle transcription result
          final text = data['text'];
          final segments = data['segments'];
          final language = data['language'];
          
          // Call your callback or use a stream controller
          _onTranscriptionReceived(text, segments, language);
          break;
          
        case 'audio_received':
          // Optional: Track buffer status
          final bufferSize = data['buffer_size'];
          final bufferDuration = data['buffer_duration'];
          break;
          
        case 'error':
          print('Error from server: ${data['message']}');
          break;
          
        case 'pong':
          print('Pong received');
          break;
          
        default:
          print('Unknown message type: $type');
      }
    } catch (e) {
      print('Error parsing message: $e');
    }
  }
  
  // Callback for transcription results
  void Function(String text, List<dynamic> segments, String? language)? 
      onTranscriptionReceived;
  
  void _onTranscriptionReceived(String text, List<dynamic> segments, String? language) {
    onTranscriptionReceived?.call(text, segments, language);
  }
  
  // Send audio data (binary)
  void sendAudio(Uint8List audioBytes) {
    if (_channel != null) {
      _channel!.sink.add(audioBytes);
    }
  }
  
  // Send JSON command
  void sendCommand(Map<String, dynamic> command) {
    if (_channel != null) {
      _channel!.sink.add(json.encode(command));
    }
  }
  
  // Disconnect
  void disconnect() {
    _channel?.sink.close();
    _channel = null;
  }
}
```

### 2. Audio Recording Setup

```dart
import 'package:record/record.dart';
import 'dart:typed_data';

class AudioRecorder {
  final AudioRecorder _recorder = AudioRecorder();
  TranscriptionService? _transcriptionService;
  bool _isRecording = false;
  
  // Initialize recorder
  Future<bool> initialize() async {
    if (await _recorder.hasPermission()) {
      return true;
    }
    return false;
  }
  
  // Start recording and streaming
  Future<void> startRecording(TranscriptionService service) async {
    if (!await initialize()) {
      throw Exception('Microphone permission denied');
    }
    
    _transcriptionService = service;
    _isRecording = true;
    
    // Configure audio format
    const config = RecordConfig(
      encoder: AudioEncoder.pcm16bits,
      sampleRate: 16000,
      numChannels: 1, // Mono
    );
    
    // Start recording stream
    final stream = await _recorder.startStream(config);
    
    // Process audio chunks
    stream.listen(
      (data) {
        if (_isRecording && _transcriptionService != null) {
          // Convert List<int> to Uint8List
          final audioBytes = Uint8List.fromList(data);
          _transcriptionService!.sendAudio(audioBytes);
        }
      },
      onError: (error) {
        print('Recording error: $error');
      },
    );
  }
  
  // Stop recording
  Future<void> stopRecording() async {
    _isRecording = false;
    await _recorder.stop();
  }
  
  // Dispose
  Future<void> dispose() async {
    await _recorder.dispose();
  }
}
```

### 3. Complete Flutter Widget Example

```dart
import 'package:flutter/material.dart';

class TranscriptionScreen extends StatefulWidget {
  @override
  _TranscriptionScreenState createState() => _TranscriptionScreenState();
}

class _TranscriptionScreenState extends State<TranscriptionScreen> {
  final TranscriptionService _service = TranscriptionService(
    serverUrl: 'ws://your-server-url:8000/ws',
  );
  final AudioRecorder _recorder = AudioRecorder();
  
  String _currentTranscription = '';
  List<String> _transcriptionHistory = [];
  bool _isConnected = false;
  bool _isRecording = false;
  
  @override
  void initState() {
    super.initState();
    _setupTranscriptionService();
  }
  
  void _setupTranscriptionService() {
    _service.onTranscriptionReceived = (text, segments, language) {
      setState(() {
        _currentTranscription = text;
        _transcriptionHistory.insert(0, text);
        if (_transcriptionHistory.length > 50) {
          _transcriptionHistory.removeLast();
        }
      });
    };
  }
  
  Future<void> _connect() async {
    await _service.connect();
    setState(() {
      _isConnected = true;
    });
  }
  
  Future<void> _startRecording() async {
    if (!_isConnected) {
      await _connect();
    }
    
    try {
      await _recorder.startRecording(_service);
      setState(() {
        _isRecording = true;
      });
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Failed to start recording: $e')),
      );
    }
  }
  
  Future<void> _stopRecording() async {
    await _recorder.stopRecording();
    setState(() {
      _isRecording = false;
    });
  }
  
  void _clearBuffer() {
    _service.sendCommand({'type': 'clear'});
    setState(() {
      _currentTranscription = '';
    });
  }
  
  @override
  void dispose() {
    _recorder.dispose();
    _service.disconnect();
    super.dispose();
  }
  
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('Real-time Transcription'),
      ),
      body: Column(
        children: [
          // Connection status
          Container(
            padding: EdgeInsets.all(16),
            color: _isConnected ? Colors.green : Colors.red,
            child: Row(
              children: [
                Icon(
                  _isConnected ? Icons.check_circle : Icons.error,
                  color: Colors.white,
                ),
                SizedBox(width: 8),
                Text(
                  _isConnected ? 'Connected' : 'Disconnected',
                  style: TextStyle(color: Colors.white),
                ),
              ],
            ),
          ),
          
          // Current transcription
          Expanded(
            child: Padding(
              padding: EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Current Transcription:',
                    style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                  ),
                  SizedBox(height: 8),
                  Expanded(
                    child: Container(
                      padding: EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        border: Border.all(color: Colors.grey),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: SingleChildScrollView(
                        child: Text(
                          _currentTranscription.isEmpty
                              ? 'No transcription yet...'
                              : _currentTranscription,
                          style: TextStyle(fontSize: 16),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          
          // Control buttons
          Padding(
            padding: EdgeInsets.all(16),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceEvenly,
              children: [
                ElevatedButton(
                  onPressed: _isRecording ? _stopRecording : _startRecording,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: _isRecording ? Colors.red : Colors.green,
                    padding: EdgeInsets.symmetric(horizontal: 32, vertical: 16),
                  ),
                  child: Row(
                    children: [
                      Icon(_isRecording ? Icons.stop : Icons.mic),
                      SizedBox(width: 8),
                      Text(_isRecording ? 'Stop' : 'Start'),
                    ],
                  ),
                ),
                ElevatedButton(
                  onPressed: _clearBuffer,
                  child: Text('Clear'),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
```

## Message Types Reference

### Sending Messages

#### 1. Audio Data (Binary)
Send raw audio bytes directly:
```dart
Uint8List audioBytes = ...; // Your audio data
_service.sendAudio(audioBytes);
```

#### 2. JSON Commands

**Ping:**
```dart
_service.sendCommand({'type': 'ping'});
```

**Clear Buffer:**
```dart
_service.sendCommand({'type': 'clear'});
```

**Get Status:**
```dart
_service.sendCommand({'type': 'status'});
```

### Receiving Messages

All server messages are JSON with a `type` field:

#### `connected`
```json
{
  "type": "connected",
  "message": "WebSocket connected. Start sending audio chunks.",
  "sample_rate": 16000,
  "format": "16-bit PCM, mono"
}
```

#### `transcription`
```json
{
  "type": "transcription",
  "text": "Hello world",
  "segments": [
    {
      "start": 0.0,
      "end": 1.5,
      "text": "Hello world"
    }
  ],
  "language": "en"
}
```

#### `audio_received`
```json
{
  "type": "audio_received",
  "buffer_size": 32000,
  "buffer_duration": 2.0
}
```

#### `error`
```json
{
  "type": "error",
  "message": "Error description"
}
```

#### `pong`
```json
{
  "type": "pong"
}
```

## Important Notes

1. **Audio Format**: Ensure your audio is exactly:
   - 16-bit PCM
   - Mono (1 channel)
   - 16,000 Hz sample rate
   - Raw bytes (no headers)

2. **Connection Management**: 
   - Always check connection status before sending
   - Handle reconnection logic for network issues
   - Clean up resources in `dispose()`

3. **Performance**:
   - Send audio in small chunks (e.g., 1024-4096 bytes)
   - Don't send too frequently to avoid overwhelming the server
   - Consider buffering if network is slow

4. **Error Handling**:
   - Always wrap WebSocket operations in try-catch
   - Handle disconnections gracefully
   - Show user-friendly error messages

5. **Testing**:
   - Test with different network conditions
   - Test with various audio sources
   - Monitor buffer status to ensure smooth streaming

## Troubleshooting

### No transcriptions received
- Check if audio format matches requirements
- Verify WebSocket connection is active
- Check server logs for errors
- Ensure microphone permissions are granted

### Connection issues
- Verify server URL is correct
- Check network connectivity
- Ensure server is running
- Check firewall settings

### Audio quality issues
- Verify sample rate is exactly 16kHz
- Ensure mono channel (not stereo)
- Check audio input levels
- Test with different audio sources

## Example: Using StreamController for Reactive Updates

```dart
import 'dart:async';

class TranscriptionService {
  final _transcriptionController = StreamController<String>.broadcast();
  Stream<String> get transcriptions => _transcriptionController.stream;
  
  void _handleMessage(dynamic message) {
    final data = json.decode(message);
    if (data['type'] == 'transcription') {
      _transcriptionController.add(data['text']);
    }
  }
  
  void dispose() {
    _transcriptionController.close();
  }
}

// In your widget:
StreamBuilder<String>(
  stream: _service.transcriptions,
  builder: (context, snapshot) {
    return Text(snapshot.data ?? 'No transcription');
  },
)
```

## Server Configuration

Make sure your server is running:
```bash
python main_realtime.py
```

Default endpoint: `ws://localhost:8000/ws`

For production, replace `localhost` with your server's IP address or domain.
