from pathlib import Path

from analyzer.topic_sources import (
    collect_filter_topics,
    parse_topics_tsv,
    parse_topics_tsv_file,
    topics_from_jellyfin_config,
)


def test_parse_topics_tsv_one_topic_per_line():
    text = """
# comment
topic
nudity
religion_christianity	crosses and churches
politics
"""
    assert parse_topics_tsv(text) == (
        "nudity",
        "religion_christianity",
        "politics",
    )


def test_parse_topics_tsv_plain_list():
    assert parse_topics_tsv("nudity\nreligion_christianity\n") == (
        "nudity",
        "religion_christianity",
    )


def test_parse_example_avoided_topics_file():
    path = Path(__file__).resolve().parents[1] / "examples" / "avoided_topics.tsv"
    topics = parse_topics_tsv_file(path)
    assert "nudity" in topics
    assert "religion_christianity" in topics
    assert "politics" in topics


def test_topics_from_jellyfin_config_unions_user_profiles():
    payload = {
        "UserProfileEntries": [
            {
                "UserId": "aaa",
                "Profile": {
                    "Topics": ["nudity", "religion_christianity"],
                },
            },
            {
                "UserId": "bbb",
                "Profile": {
                    "topics": ["nudity", "politics"],
                },
            },
        ]
    }
    assert topics_from_jellyfin_config(payload) == (
        "nudity",
        "religion_christianity",
        "politics",
    )


def test_topics_from_jellyfin_config_includes_custom_topics():
    payload = {
        "UserProfileEntries": [
            {"UserId": "aaa", "Profile": {"Topics": ["nudity"]}},
        ],
        "CustomTopics": ["asian people", "lesbians"],
    }
    assert topics_from_jellyfin_config(payload) == (
        "nudity",
        "asian people",
        "lesbians",
    )


def test_topics_from_jellyfin_xml_reads_custom_and_user_topics(tmp_path: Path):
    path = tmp_path / "Jellyfin.Plugin.SmartBranching.xml"
    path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<PluginConfiguration>
  <UserProfileEntries>
    <UserProfileEntry>
      <UserId>aaa</UserId>
      <Profile>
        <Topics>
          <Topic>asian people</Topic>
        </Topics>
      </Profile>
    </UserProfileEntry>
  </UserProfileEntries>
  <CustomTopics>
    <Topic>asian people</Topic>
    <Topic>black people</Topic>
    <Topic>lesbians</Topic>
  </CustomTopics>
</PluginConfiguration>
"""
    )
    from analyzer.topic_sources import topics_from_jellyfin_xml

    assert topics_from_jellyfin_xml(path) == (
        "asian people",
        "black people",
        "lesbians",
    )


def test_collect_filter_topics_reads_jellyfin_xml(tmp_path: Path):
    path = tmp_path / "plugin.xml"
    path.write_text(
        """<?xml version="1.0"?>
<PluginConfiguration>
  <CustomTopics>
    <Topic>asian people</Topic>
  </CustomTopics>
</PluginConfiguration>
"""
    )
    assert collect_filter_topics(jellyfin_config=path) == ("asian people",)


def test_collect_filter_topics_unions_file_and_extra(tmp_path: Path):
    path = tmp_path / "topics.tsv"
    path.write_text("nudity\n")
    assert collect_filter_topics(
        topics_file=path,
        extra=("religion_christianity",),
    ) == ("nudity", "religion_christianity")
