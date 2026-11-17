import unittest

from lyrik.lyrics import Lyrics, LyricLine, parse_lrc


class ParseLrcTests(unittest.TestCase):
    def test_parses_standard_lines(self) -> None:
        raw = "[00:12.40]First line\n[00:15.00]Second line\n"
        lines = parse_lrc(raw)
        self.assertEqual(lines[0], LyricLine(12.4, "First line"))
        self.assertEqual(lines[1].text, "Second line")
        self.assertEqual(lines[1].time, 15.0)

    def test_ignores_empty_and_metadata(self) -> None:
        raw = "[ar:Artist]\n[00:01.00]\n[00:02.00]♪\n[00:03.00]Hello\n"
        lines = parse_lrc(raw)
        self.assertEqual([line.text for line in lines], ["Hello"])

    def test_multiple_timestamps(self) -> None:
        raw = "[00:01.00][00:10.00]Chorus\n"
        lines = parse_lrc(raw)
        self.assertEqual([line.time for line in lines], [1.0, 10.0])
        self.assertEqual({line.text for line in lines}, {"Chorus"})

    def test_line_index_at(self) -> None:
        lyrics = Lyrics(
            synced=[
                LyricLine(0.0, "a"),
                LyricLine(5.0, "b"),
                LyricLine(9.5, "c"),
            ]
        )
        self.assertEqual(lyrics.line_index_at(0.0), 0)
        self.assertEqual(lyrics.line_index_at(5.2), 1)
        self.assertEqual(lyrics.line_index_at(20.0), 2)


if __name__ == "__main__":
    unittest.main()
