using System.Collections.Generic;
using System.IO;
using System.Xml;
using System.Xml.Serialization;
using Jellyfin.Plugin.SmartBranching.Configuration;
using Xunit;

namespace SmartBranching.Plugin.Tests;

public class PluginConfigurationSerializationTests
{
    [Fact]
    public void Configuration_RoundTrips_Through_XmlSerializer_Without_Dictionary_Member()
    {
        var config = new PluginConfiguration
        {
            Enabled = true,
            DefaultProfile = "child",
            FillerDirectory = "smart_branching/filler",
            NsfwThreshold = 0.75f,
            DefaultAction = "swap",
        };
        config.SetUserProfiles(new Dictionary<string, UserBranchProfile>
        {
            ["user-1"] = new()
            {
                Birthday = "2016-01-01",
                Sex = "female",
                ProfileOverride = null,
            },
            ["user-2"] = new()
            {
                Birthday = "2008-05-20",
                Sex = "male",
                ProfileOverride = "teen_m",
            },
        });

        var serializer = new XmlSerializer(typeof(PluginConfiguration));
        using var writer = new StringWriter();
        using var xmlWriter = XmlWriter.Create(writer, new XmlWriterSettings { OmitXmlDeclaration = false });
        serializer.Serialize(xmlWriter, config);
        var xml = writer.ToString();

        Assert.Contains("UserProfileEntries", xml);
        Assert.DoesNotContain("UserProfiles", xml);

        using var reader = new StringReader(xml);
        var deserialized = (PluginConfiguration)serializer.Deserialize(reader)!;

        Assert.True(deserialized.Enabled);
        Assert.Equal("child", deserialized.DefaultProfile);
        Assert.Equal(2, deserialized.UserProfileEntries.Count);
        Assert.True(deserialized.TryGetUserProfile("user-1", out var first));
        Assert.Equal("female", first.Sex);
        Assert.True(deserialized.TryGetUserProfile("user-2", out var second));
        Assert.Equal("teen_m", second.ProfileOverride);
        Assert.False(deserialized.TryGetUserProfile("missing", out _));
    }

    [Fact]
    public void TryGetUserProfile_MatchesGuidWithOrWithoutDashes()
    {
        var userId = "2c7f8e431a7c43a19b8656aa52354fc6";
        var config = new PluginConfiguration();
        config.SetUserProfiles(new Dictionary<string, UserBranchProfile>
        {
            [userId] = new()
            {
                ProfileOverride = "adult",
            }
        });

        Assert.True(config.TryGetUserProfile("2c7f8e431a7c43a19b8656aa52354fc6", out var dashed));
        Assert.Equal("adult", dashed.ProfileOverride);
        Assert.True(config.TryGetUserProfile(userId, out var compact));
        Assert.Equal("adult", compact.ProfileOverride);
    }
}
