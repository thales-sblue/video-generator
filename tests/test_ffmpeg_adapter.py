import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from video_generator.adapters import (
    CaptionCue,
    FFmpegError,
    SequenceClip,
    SequenceImage,
    compose_video_sequence,
    extract_audio,
    extract_segment,
)


class FFmpegAdapterTests(unittest.TestCase):
    def test_loops_and_mixes_music_under_narration_with_persisted_gain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            narration = root / "narration.wav"
            music = root / "music.wav"
            output = root / "mixed.mp4"
            for path in (first, second, narration, music):
                path.write_bytes(path.stem.encode("utf-8"))

            def succeed(command, **kwargs):
                Path(command[-1]).write_bytes(b"mixed sequence")
                return subprocess.CompletedProcess(command, 0, "", "")

            clips = (SequenceClip(str(first), 0, 1), SequenceClip(str(second), 0, 2))
            with patch("video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=succeed
            ) as execute:
                artifact = compose_video_sequence(
                    clips,
                    output,
                    narration_path=narration,
                    music_path=music,
                    music_gain_db=-18,
                )

            command = execute.call_args.args[0]
            inputs = [command[index + 1] for index, value in enumerate(command) if value == "-i"]
            self.assertEqual(
                inputs,
                [str(first.resolve()), str(second.resolve()), str(narration.resolve()), str(music.resolve())],
            )
            self.assertEqual(command[command.index("-stream_loop") + 1], "-1")
            self.assertLess(command.index("-stream_loop"), command.index(str(music.resolve())))
            filter_graph = command[command.index("-filter_complex") + 1]
            self.assertIn("[3:a:0]aresample=48000", filter_graph)
            self.assertIn("volume=-18dB", filter_graph)
            self.assertIn("[voice][bed]amix=inputs=2", filter_graph)
            self.assertIn("alimiter=limit=0.95:latency=1[outa]", filter_graph)
            self.assertEqual(artifact.music_source_path, str(music.resolve()))
            self.assertEqual(artifact.music_gain_db, -18.0)

    def test_burns_caption_cues_from_a_temporary_srt_without_filter_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            output = root / "captioned.mp4"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            captured_srt = []

            def succeed(command, **kwargs):
                caption_files = list(root.glob(".*-captions-*.srt"))
                self.assertEqual(len(caption_files), 1)
                captured_srt.append(caption_files[0].read_text(encoding="utf-8"))
                Path(command[-1]).write_bytes(b"captioned sequence")
                return subprocess.CompletedProcess(command, 0, "", "")

            clips = (SequenceClip(str(first), 0, 1), SequenceClip(str(second), 0, 1))
            cues = (
                CaptionCue("Why: now, not later?", 0, 1),
                CaptionCue("Because captions help.", 1, 2),
            )
            with patch("video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=succeed
            ) as execute:
                artifact = compose_video_sequence(clips, output, captions=cues)

            command = execute.call_args.args[0]
            filter_graph = command[command.index("-filter_complex") + 1]
            self.assertIn("[basev]subtitles=filename=", filter_graph)
            self.assertIn("BorderStyle=3", filter_graph)
            self.assertNotIn(cues[0].text, filter_graph)
            self.assertIn("00:00:00,000 --> 00:00:01,000", captured_srt[0])
            self.assertIn(cues[0].text, captured_srt[0])
            self.assertEqual(artifact.caption_count, 2)
            self.assertEqual(list(root.glob(".*-captions-*.srt")), [])

    def test_composes_video_with_narration_after_clip_inputs_and_expected_codecs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            narration = root / "narration.wav"
            output = root / "narrated.mp4"
            for path, content in (
                (first, b"first"), (second, b"second"), (narration, b"narration")
            ):
                path.write_bytes(content)

            def succeed(command, **kwargs):
                Path(command[-1]).write_bytes(b"narrated sequence")
                return subprocess.CompletedProcess(command, 0, "", "")

            clips = (SequenceClip(str(first), 0, 1), SequenceClip(str(second), 0, 2))
            with patch("video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=succeed
            ) as execute:
                artifact = compose_video_sequence(
                    clips, output, narration_path=narration, timeout_seconds=20
                )

            command = execute.call_args.args[0]
            inputs = [command[index + 1] for index, value in enumerate(command) if value == "-i"]
            self.assertEqual(
                inputs,
                [str(first.resolve()), str(second.resolve()), str(narration.resolve())],
            )
            filter_graph = command[command.index("-filter_complex") + 1]
            self.assertIn("[2:a:0]aresample=48000", filter_graph)
            self.assertIn("apad,atrim=duration=3", filter_graph)
            self.assertEqual(command[command.index("-c:v") + 1], "libopenh264")
            self.assertEqual(command[command.index("-c:a") + 1], "aac")
            self.assertNotIn("-an", command)
            self.assertFalse(execute.call_args.kwargs["shell"])
            self.assertEqual(artifact.narration_source_path, str(narration.resolve()))
            self.assertEqual(
                (first.read_bytes(), second.read_bytes(), narration.read_bytes()),
                (b"first", b"second", b"narration"),
            )

    def test_composes_an_ordered_silent_video_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            output = root / "timeline.mp4"
            first.write_bytes(b"first source")
            second.write_bytes(b"second source")

            def succeed(command, **kwargs):
                Path(command[-1]).write_bytes(b"composed sequence")
                return subprocess.CompletedProcess(command, 0, "", "")

            clips = (SequenceClip(str(first), 1, 2.5), SequenceClip(str(second), 0, 2))
            with patch("video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=succeed
            ) as execute:
                artifact = compose_video_sequence(clips, output, timeout_seconds=20)

            command = execute.call_args.args[0]
            self.assertEqual(command.count("-i"), 2)
            self.assertLess(command.index(str(first.resolve())), command.index(str(second.resolve())))
            filter_graph = command[command.index("-filter_complex") + 1]
            self.assertIn("[0:v:0]trim=start=1:end=2.5,setpts=PTS-STARTPTS[v0]", filter_graph)
            self.assertIn("[v0][v1]concat=n=2:v=1:a=0[outv]", filter_graph)
            self.assertEqual(command[command.index("-c:v") + 1], "libopenh264")
            self.assertIn("-an", command)
            self.assertFalse(execute.call_args.kwargs["shell"])
            self.assertEqual(first.read_bytes(), b"first source")
            self.assertEqual(second.read_bytes(), b"second source")
            self.assertEqual(artifact.duration_seconds, 3.5)
            self.assertEqual(artifact.source_paths, (str(first.resolve()), str(second.resolve())))
            self.assertEqual(output.read_bytes(), b"composed sequence")

    def test_composes_a_still_image_between_video_clips(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            still = root / "card.png"
            second = root / "second.mp4"
            output = root / "timeline.mp4"
            for path in (first, still, second):
                path.write_bytes(path.stem.encode("utf-8"))

            def succeed(command, **kwargs):
                Path(command[-1]).write_bytes(b"composed sequence")
                return subprocess.CompletedProcess(command, 0, "", "")

            timeline = (
                SequenceClip(str(first), 0, 1.5),
                SequenceImage(str(still), 4),
                SequenceClip(str(second), 0, 2),
            )
            with patch(
                "video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"
            ), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=succeed
            ) as execute:
                artifact = compose_video_sequence(timeline, output, timeout_seconds=20)

            command = execute.call_args.args[0]
            self.assertEqual(command.count("-i"), 3)
            # the still is looped for exactly its declared duration
            loop_index = command.index("-loop")
            self.assertEqual(command[loop_index + 1], "1")
            self.assertEqual(command[loop_index + 2], "-t")
            self.assertEqual(command[loop_index + 3], "4")
            self.assertEqual(command[loop_index + 4], "-i")
            self.assertEqual(command[loop_index + 5], str(still.resolve()))
            filter_graph = command[command.index("-filter_complex") + 1]
            self.assertIn(
                "[1:v:0]fps=30,setsar=1,format=yuv420p,trim=duration=4,setpts=PTS-STARTPTS[v1]",
                filter_graph,
            )
            # video segments are normalised too so the concat is deterministic
            self.assertIn(
                "[0:v:0]trim=start=0:end=1.5,setpts=PTS-STARTPTS,fps=30,setsar=1,format=yuv420p[v0]",
                filter_graph,
            )
            self.assertIn("[v0][v1][v2]concat=n=3:v=1:a=0[outv]", filter_graph)
            self.assertEqual(artifact.duration_seconds, 7.5)
            self.assertEqual(artifact.image_count, 1)
            self.assertEqual(
                artifact.source_paths,
                (str(first.resolve()), str(still.resolve()), str(second.resolve())),
            )

    def test_sequence_requires_at_least_one_video_clip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            one = root / "one.png"
            two = root / "two.png"
            for path in (one, two):
                path.write_bytes(path.stem.encode("utf-8"))
            with patch("video_generator.adapters.ffmpeg.resolve_media_tool") as resolve:
                with self.assertRaisesRegex(FFmpegError, "at least one video"):
                    compose_video_sequence(
                        (SequenceImage(str(one), 2), SequenceImage(str(two), 2)),
                        root / "out.mp4",
                    )
            resolve.assert_not_called()

    def test_sequence_rejects_a_non_positive_image_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clip = root / "clip.mp4"
            still = root / "still.png"
            for path in (clip, still):
                path.write_bytes(path.stem.encode("utf-8"))
            with patch("video_generator.adapters.ffmpeg.resolve_media_tool"):
                with self.assertRaisesRegex(FFmpegError, "image duration_seconds"):
                    compose_video_sequence(
                        (SequenceClip(str(clip), 0, 1), SequenceImage(str(still), 0)),
                        root / "out.mp4",
                    )

    def test_sequence_rejects_unsafe_or_incomplete_inputs_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            other = root / "other.mp4"
            source.write_bytes(b"source")
            other.write_bytes(b"other")
            with patch("video_generator.adapters.ffmpeg.resolve_media_tool") as resolve:
                with self.assertRaisesRegex(FFmpegError, "at least two"):
                    compose_video_sequence((SequenceClip(str(source), 0, 1),), root / "out.mp4")
                with self.assertRaisesRegex(FFmpegError, "must not overwrite"):
                    compose_video_sequence(
                        (SequenceClip(str(source), 0, 1), SequenceClip(str(other), 0, 1)),
                        source,
                    )
                with self.assertRaisesRegex(FFmpegError, "greater than"):
                    compose_video_sequence(
                        (SequenceClip(str(source), 1, 1), SequenceClip(str(other), 0, 1)),
                        root / "out.mp4",
                    )
                with self.assertRaisesRegex(FFmpegError, "narration does not exist"):
                    compose_video_sequence(
                        (SequenceClip(str(source), 0, 1), SequenceClip(str(other), 0, 1)),
                        root / "out.mp4",
                        narration_path=root / "missing.wav",
                    )
                narration = root / "narration.mp4"
                narration.write_bytes(b"narration")
                with self.assertRaisesRegex(FFmpegError, "narration source"):
                    compose_video_sequence(
                        (SequenceClip(str(source), 0, 1), SequenceClip(str(other), 0, 1)),
                        narration,
                        narration_path=narration,
                    )
                with self.assertRaisesRegex(FFmpegError, "music does not exist"):
                    compose_video_sequence(
                        (SequenceClip(str(source), 0, 1), SequenceClip(str(other), 0, 1)),
                        root / "out.mp4",
                        music_path=root / "missing.wav",
                        music_gain_db=-18,
                    )
                music = root / "music.wav"
                music.write_bytes(b"music")
                with self.assertRaisesRegex(FFmpegError, "from -60 to 0"):
                    compose_video_sequence(
                        (SequenceClip(str(source), 0, 1), SequenceClip(str(other), 0, 1)),
                        root / "out.mp4",
                        music_path=music,
                        music_gain_db=3,
                    )
                with self.assertRaisesRegex(FFmpegError, "requires a music_path"):
                    compose_video_sequence(
                        (SequenceClip(str(source), 0, 1), SequenceClip(str(other), 0, 1)),
                        root / "out.mp4",
                        music_gain_db=-18,
                    )
                existing = root / "existing.mp4"
                existing.write_bytes(b"existing")
                with self.assertRaisesRegex(FFmpegError, "already exists"):
                    compose_video_sequence(
                        (SequenceClip(str(source), 0, 1), SequenceClip(str(other), 0, 1)),
                        existing,
                    )
                with self.assertRaisesRegex(FFmpegError, "must not exceed"):
                    compose_video_sequence(
                        (SequenceClip(str(source), 0, 1), SequenceClip(str(other), 0, 1)),
                        root / "out.mp4",
                        captions=(CaptionCue("Too late", 1, 2.1),),
                    )
                with self.assertRaisesRegex(FFmpegError, "markup"):
                    compose_video_sequence(
                        (SequenceClip(str(source), 0, 1), SequenceClip(str(other), 0, 1)),
                        root / "out.mp4",
                        captions=(CaptionCue("{\\an8}Injected style", 0, 1),),
                    )
            resolve.assert_not_called()

    def test_narrated_sequence_removes_partial_or_empty_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            narration = root / "narration.wav"
            for path in (first, second, narration):
                path.write_bytes(b"source")
            clips = (SequenceClip(str(first), 0, 1), SequenceClip(str(second), 0, 1))

            def fail(command, **kwargs):
                Path(command[-1]).write_bytes(b"partial")
                return subprocess.CompletedProcess(command, 1, "", "invalid audio")

            with patch("video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=fail
            ):
                with self.assertRaisesRegex(FFmpegError, "invalid audio"):
                    compose_video_sequence(
                        clips,
                        root / "failed.mp4",
                        narration_path=narration,
                        captions=(CaptionCue("Caption", 0, 2),),
                    )
            with patch("video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ):
                with self.assertRaisesRegex(FFmpegError, "non-empty sequence"):
                    compose_video_sequence(clips, root / "empty.mp4", narration_path=narration)

            self.assertFalse((root / "failed.mp4").exists())
            self.assertFalse((root / "empty.mp4").exists())
            self.assertEqual(list(root.glob(".*.mp4")), [])
            self.assertEqual(list(root.glob(".*-captions-*.srt")), [])

    def test_extracts_first_audio_stream_as_deterministic_pcm_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "audio.wav"
            source.write_bytes(b"source")

            def succeed(command, **kwargs):
                Path(command[-1]).write_bytes(b"wav-data")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=succeed
            ) as execute:
                artifact = extract_audio(source, output, timeout_seconds=20)

            command = execute.call_args.args[0]
            self.assertEqual(
                command,
                [
                    "ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(source.resolve()),
                    "-map", "0:a:0", "-vn", "-sn", "-dn", "-c:a", "pcm_s16le",
                    "-ar", "48000", "-ac", "2", command[-1],
                ],
            )
            self.assertFalse(execute.call_args.kwargs["shell"])
            self.assertEqual(execute.call_args.kwargs["timeout"], 20)
            self.assertEqual(artifact.source_path, str(source.resolve()))
            self.assertEqual(artifact.output_path, str(output.resolve()))
            self.assertEqual((artifact.sample_rate_hz, artifact.channels), (48000, 2))
            self.assertEqual(output.read_bytes(), b"wav-data")

    def test_audio_extraction_rejects_unsafe_targets_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            execute = Mock()
            with patch("video_generator.adapters.ffmpeg.subprocess.run", execute):
                with self.assertRaisesRegex(FFmpegError, "requires a .wav"):
                    extract_audio(source, root / "audio.mp3")
                existing = root / "audio.wav"
                existing.write_bytes(b"existing")
                with self.assertRaisesRegex(FFmpegError, "already exists"):
                    extract_audio(source, existing)
                with self.assertRaisesRegex(FFmpegError, "greater than zero"):
                    extract_audio(source, root / "new.wav", timeout_seconds=0)
            execute.assert_not_called()

    def test_audio_extraction_removes_partial_artifact_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "audio.wav"
            source.write_bytes(b"source")

            def fail(command, **kwargs):
                Path(command[-1]).write_bytes(b"partial")
                return subprocess.CompletedProcess(command, 1, "", "no audio stream")

            with patch("video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=fail
            ):
                with self.assertRaisesRegex(FFmpegError, "no audio stream"):
                    extract_audio(source, output)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".*.wav")), [])

    def test_extracts_segment_with_structured_stream_copy_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "nested" / "segment.mp4"
            source.write_bytes(b"immutable source")

            def run(command, **kwargs):
                Path(command[-1]).write_bytes(b"segment")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("video_generator.adapters.ffmpeg.resolve_media_tool", return_value="C:/tools/ffmpeg.exe"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=run
            ) as execute:
                artifact = extract_segment(source, output, start_seconds=1.5, end_seconds=4)

            command = execute.call_args.args[0]
            self.assertEqual(command[:5], ["C:/tools/ffmpeg.exe", "-v", "error", "-nostdin", "-y"])
            self.assertEqual(command[command.index("-ss") + 1], "1.5")
            self.assertEqual(command[command.index("-t") + 1], "2.5")
            self.assertEqual(command[command.index("-c") + 1], "copy")
            self.assertFalse(execute.call_args.kwargs["shell"])
            self.assertEqual(source.read_bytes(), b"immutable source")
            self.assertEqual(output.read_bytes(), b"segment")
            self.assertEqual(artifact.file_size_bytes, 7)
            self.assertEqual(artifact.output_path, str(output.resolve()))
            self.assertEqual(artifact.mode, "copy")

    def test_precise_mode_seeks_after_input_and_reencodes_mp4(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mov"
            output = root / "segment.mp4"
            source.write_bytes(b"immutable source")

            def run(command, **kwargs):
                Path(command[-1]).write_bytes(b"precise segment")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=run
            ) as execute:
                artifact = extract_segment(
                    source,
                    output,
                    start_seconds=1.25,
                    end_seconds=2.75,
                    mode="precise",
                )

            command = execute.call_args.args[0]
            self.assertLess(command.index("-i"), command.index("-ss"))
            self.assertEqual(command[command.index("-c:v") + 1], "libopenh264")
            self.assertEqual(command[command.index("-c:a") + 1], "aac")
            self.assertIn("0:v:0?", command)
            self.assertIn("0:a:0?", command)
            self.assertEqual(artifact.mode, "precise")
            self.assertEqual(source.read_bytes(), b"immutable source")

    def test_rejects_invalid_mode_and_non_mp4_precise_output_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"source")

            with patch("video_generator.adapters.ffmpeg.resolve_media_tool") as resolve:
                with self.assertRaisesRegex(FFmpegError, "mode must"):
                    extract_segment(
                        source,
                        Path(directory) / "output.mp4",
                        start_seconds=0,
                        end_seconds=1,
                        mode=["precise"],
                    )
                with self.assertRaisesRegex(FFmpegError, "requires an .mp4"):
                    extract_segment(
                        source,
                        Path(directory) / "output.mkv",
                        start_seconds=0,
                        end_seconds=1,
                        mode="precise",
                    )

            resolve.assert_not_called()

    def test_rejects_source_overwrite_existing_output_and_invalid_times(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            output = Path(directory) / "output.mp4"
            source.write_bytes(b"source")
            output.write_bytes(b"existing")

            with patch("video_generator.adapters.ffmpeg.subprocess.run") as execute:
                with self.assertRaisesRegex(FFmpegError, "must not overwrite"):
                    extract_segment(source, source, start_seconds=0, end_seconds=1)
                with self.assertRaisesRegex(FFmpegError, "already exists"):
                    extract_segment(source, output, start_seconds=0, end_seconds=1)
                with self.assertRaisesRegex(FFmpegError, "greater than"):
                    extract_segment(source, Path(directory) / "new.mp4", start_seconds=2, end_seconds=1)

            execute.assert_not_called()

    def test_reports_missing_tool_without_creating_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            output = Path(directory) / "new" / "segment.mp4"
            source.write_bytes(b"source")

            with patch("video_generator.adapters.ffmpeg.resolve_media_tool", return_value=None):
                with self.assertRaisesRegex(FFmpegError, "not available"):
                    extract_segment(source, output, start_seconds=0, end_seconds=1)

            self.assertFalse(output.parent.exists())

    def test_removes_partial_artifact_when_ffmpeg_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "output.mp4"
            source.write_bytes(b"source")

            def fail(command, **kwargs):
                Path(command[-1]).write_bytes(b"partial")
                return subprocess.CompletedProcess(command, 1, "", "bad input")

            with patch("video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run", side_effect=fail
            ):
                with self.assertRaisesRegex(FFmpegError, "bad input"):
                    extract_segment(source, output, start_seconds=0, end_seconds=1)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".*.mp4")), [])

    def test_rejects_empty_artifact_when_ffmpeg_reports_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "output.mp4"
            source.write_bytes(b"source")

            with patch("video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ):
                with self.assertRaisesRegex(FFmpegError, "non-empty artifact"):
                    extract_segment(source, output, start_seconds=0, end_seconds=1)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".*.mp4")), [])

    def test_removes_reserved_artifact_after_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "output.mp4"
            source.write_bytes(b"source")

            with patch("video_generator.adapters.ffmpeg.resolve_media_tool", return_value="ffmpeg"), patch(
                "video_generator.adapters.ffmpeg.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["ffmpeg"], 1),
            ):
                with self.assertRaisesRegex(FFmpegError, "timed out"):
                    extract_segment(source, output, start_seconds=0, end_seconds=1, timeout_seconds=1)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".*.mp4")), [])


if __name__ == "__main__":
    unittest.main()
