// Template code mod: a new actor type that maps can place as entity "GabTemplateLight" (a classname in a .map file).
// New C# files are compiled into the game; nothing here changes the stock game until a map uses the entity.
namespace Celeste64;

public class GabTemplateLight : Actor
{
	public float Pulse;

	// runs when the game's assembly loads: register the map entity name (Map.ActorFactories is the game's own table)
	[System.Runtime.CompilerServices.ModuleInitializer]
	internal static void Register() => Map.ActorFactories["GabTemplateLight"] = new((map, entity) => new GabTemplateLight());

	public override void Update() => Pulse += Time.Delta;
}
