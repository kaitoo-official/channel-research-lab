"""YouTube Channel Analyze (ytca) — channel-wide knowledge analysis from human-provided material.

Design rule: this package never touches the network. It does not call YouTube (pages, RSS, oEmbed, API),
does not download media, and does not read cookies. Every input (channel URL, video URLs, notes,
NotebookLM summaries, authorized transcripts, CLAIM/EVIDENCE analyses) is handed to it by a person
or by a Claude session that read only those inputs. See docs/policy.md.
"""
SCHEMA_VERSION = 1
