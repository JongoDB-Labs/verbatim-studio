import { useState, useRef, useCallback, useEffect } from 'react';

interface AudioRecorderProps {
  onRecordingComplete: (blob: Blob, filename: string) => void;
  onCancel?: () => void;
  audioBitsPerSecond?: number;
  autoStart?: boolean;
}

type RecordingState = 'idle' | 'recording' | 'paused' | 'stopped';

export function AudioRecorder({ onRecordingComplete, onCancel, audioBitsPerSecond, autoStart }: AudioRecorderProps) {
  const [state, setState] = useState<RecordingState>('idle');
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [audioLevel, setAudioLevel] = useState(0);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<number | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animationRef = useRef<number | null>(null);
  const cancelledRef = useRef(false);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (animationRef.current) cancelAnimationFrame(animationRef.current);
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop());
      }
    };
  }, []);

  const formatDuration = (seconds: number): string => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  const updateAudioLevel = useCallback(() => {
    if (!analyserRef.current) return;

    const dataArray = new Uint8Array(analyserRef.current.frequencyBinCount);
    analyserRef.current.getByteFrequencyData(dataArray);

    // Calculate average level
    const average = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
    setAudioLevel(average / 255); // Normalize to 0-1

    if (state === 'recording') {
      animationRef.current = requestAnimationFrame(updateAudioLevel);
    }
  }, [state]);

  const startRecording = async () => {
    try {
      setError(null);
      cancelledRef.current = false;
      chunksRef.current = [];

      // Request microphone access
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          sampleRate: 44100,
        },
      });
      streamRef.current = stream;

      // Set up audio analyser for level meter
      const audioContext = new AudioContext();
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      analyserRef.current = analyser;

      // Determine best supported format
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : 'audio/mp4';

      // Create MediaRecorder
      const mediaRecorder = new MediaRecorder(stream, {
        mimeType,
        ...(audioBitsPerSecond ? { audioBitsPerSecond } : {}),
      });
      mediaRecorderRef.current = mediaRecorder;

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = () => {
        if (cancelledRef.current) {
          cancelledRef.current = false;
          return;
        }
        // Create blob from chunks
        const blob = new Blob(chunksRef.current, { type: mimeType });
        const extension = mimeType.includes('webm') ? 'webm' : 'm4a';
        const filename = `recording-${new Date().toISOString().slice(0, 19).replace(/[:.]/g, '-')}.${extension}`;
        onRecordingComplete(blob, filename);
      };

      // Start recording
      mediaRecorder.start(1000); // Collect data every second
      setState('recording');

      // Start duration timer
      timerRef.current = window.setInterval(() => {
        setDuration((d) => d + 1);
      }, 1000);

      // Start audio level updates
      updateAudioLevel();
    } catch (err) {
      console.error('Failed to start recording:', err);
      if (err instanceof DOMException) {
        switch (err.name) {
          case 'NotAllowedError':
            setError('Microphone access denied. Please allow microphone access in your system settings.');
            break;
          case 'NotFoundError':
            setError('No microphone found. Please connect a microphone and try again.');
            break;
          case 'NotReadableError':
            setError('Microphone is in use by another application. Please close it and try again.');
            break;
          default:
            setError(`Recording failed: ${err.name} — ${err.message}`);
        }
      } else {
        setError(`Failed to start recording: ${err instanceof Error ? err.message : 'Unknown error'}`);
      }
    }
  };

  // Auto-start recording when mounted with autoStart prop
  const autoStartedRef = useRef(false);
  useEffect(() => {
    if (autoStart && !autoStartedRef.current) {
      autoStartedRef.current = true;
      startRecording();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart]);

  const pauseRecording = () => {
    if (mediaRecorderRef.current && state === 'recording') {
      mediaRecorderRef.current.pause();
      setState('paused');
      if (timerRef.current) clearInterval(timerRef.current);
      if (animationRef.current) cancelAnimationFrame(animationRef.current);
    }
  };

  const resumeRecording = () => {
    if (mediaRecorderRef.current && state === 'paused') {
      mediaRecorderRef.current.resume();
      setState('recording');
      timerRef.current = window.setInterval(() => {
        setDuration((d) => d + 1);
      }, 1000);
      updateAudioLevel();
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && (state === 'recording' || state === 'paused')) {
      mediaRecorderRef.current.stop();
      setState('stopped');
      if (timerRef.current) clearInterval(timerRef.current);
      if (animationRef.current) cancelAnimationFrame(animationRef.current);

      // Stop all tracks
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop());
      }
    }
  };

  const cancelRecording = () => {
    cancelledRef.current = true;
    if (timerRef.current) clearInterval(timerRef.current);
    if (animationRef.current) cancelAnimationFrame(animationRef.current);
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
    }
    setState('idle');
    setDuration(0);
    chunksRef.current = [];
    onCancel?.();
  };

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-6">
      <div className="text-center">
        {/* Title */}
        <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-4">
          {state === 'idle' ? 'Record Audio' : 'Recording'}
        </h3>

        {/* Error message */}
        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
          </div>
        )}

        {/* Recording visualization */}
        <div className="mb-6">
          {state === 'idle' ? (
            <div className="w-24 h-24 mx-auto rounded-full bg-gray-100 dark:bg-gray-700 flex items-center justify-center">
              <svg
                className="w-12 h-12 text-gray-400"
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"
                />
              </svg>
            </div>
          ) : (
            <div className="relative w-24 h-24 mx-auto">
              {/* Animated rings based on audio level */}
              <div
                className="absolute inset-0 rounded-full bg-red-500/20 transition-transform"
                style={{
                  transform: `scale(${1 + audioLevel * 0.5})`,
                  opacity: state === 'recording' ? 0.5 : 0.2,
                }}
              />
              <div
                className="absolute inset-2 rounded-full bg-red-500/30 transition-transform"
                style={{
                  transform: `scale(${1 + audioLevel * 0.3})`,
                  opacity: state === 'recording' ? 0.7 : 0.3,
                }}
              />
              <div
                className={`absolute inset-4 rounded-full flex items-center justify-center ${
                  state === 'recording' ? 'bg-red-500' : 'bg-red-400'
                }`}
              >
                {state === 'recording' ? (
                  <div className="w-4 h-4 rounded-sm bg-white animate-pulse" />
                ) : (
                  <div className="w-4 h-4 rounded-sm bg-white" />
                )}
              </div>
            </div>
          )}
        </div>

        {/* Duration */}
        {state !== 'idle' && (
          <div className="mb-6">
            <span className="text-3xl font-mono font-bold text-gray-900 dark:text-gray-100">
              {formatDuration(duration)}
            </span>
            {state === 'paused' && (
              <span className="ml-2 text-sm text-yellow-600 dark:text-yellow-400">(Paused)</span>
            )}
          </div>
        )}

        {/* Controls */}
        <div className="flex items-center justify-center gap-3">
          {state === 'idle' && (
            <button
              onClick={startRecording}
              className="inline-flex items-center gap-2 rounded-lg bg-destructive px-6 py-3 text-sm font-medium text-destructive-foreground hover:bg-destructive/90 transition-colors"
            >
              <svg
                className="w-5 h-5"
                xmlns="http://www.w3.org/2000/svg"
                fill="currentColor"
                viewBox="0 0 24 24"
              >
                <circle cx="12" cy="12" r="8" />
              </svg>
              Start Recording
            </button>
          )}

          {state === 'recording' && (
            <>
              <button
                onClick={pauseRecording}
                className="inline-flex items-center gap-2 rounded-lg bg-warning px-4 py-2.5 text-sm font-medium text-warning-foreground hover:bg-warning/90 transition-colors"
              >
                <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="4" width="4" height="16" rx="1" />
                  <rect x="14" y="4" width="4" height="16" rx="1" />
                </svg>
                Pause
              </button>
              <button
                onClick={stopRecording}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="6" width="12" height="12" rx="1" />
                </svg>
                Stop & Save
              </button>
            </>
          )}

          {state === 'paused' && (
            <>
              <button
                onClick={resumeRecording}
                className="inline-flex items-center gap-2 rounded-lg bg-success px-4 py-2.5 text-sm font-medium text-success-foreground hover:bg-success/90 transition-colors"
              >
                <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z" />
                </svg>
                Resume
              </button>
              <button
                onClick={stopRecording}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="6" width="12" height="12" rx="1" />
                </svg>
                Stop & Save
              </button>
            </>
          )}

          {(state === 'recording' || state === 'paused') && (
            <button
              onClick={cancelRecording}
              className="inline-flex items-center gap-2 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2.5 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
            >
              Cancel
            </button>
          )}
        </div>

        {/* Help text */}
        {state === 'idle' && (
          <p className="mt-4 text-sm text-gray-500 dark:text-gray-400">
            Click to start recording from your microphone
          </p>
        )}
      </div>
    </div>
  );
}
