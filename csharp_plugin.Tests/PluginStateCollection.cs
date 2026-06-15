using Xunit;

namespace SmartBranching.Plugin.Tests;

[CollectionDefinition(Name, DisableParallelization = true)]
public sealed class PluginStateCollection
{
    public const string Name = "PluginState";
}
