// gab state self-check test mod: a new actor type (placed by the test map as entity "GabBeacon"), a save-record flag, a
// static variable and a line of HUD text -- all three must show up in state() (mod / screen_text) without the mod reporting
namespace Celeste64;

public class GabBeacon : Actor, IHaveUI
{
	public static string QuestStage = "stage_two";
	public string Label = "beacon_alpha";
	public int Charge = 3;

	[System.Runtime.CompilerServices.ModuleInitializer]
	internal static void Register() => Map.ActorFactories["GabBeacon"] = new((map, entity) => new GabBeacon());

	public override void Added() => Save.CurrentRecord.SetFlag("gab_visited_shrine", 7);

	public void RenderUI(Batcher batch, Rect bounds) =>
		UI.Text(batch, "GAB TEST HUD: the beacon is near", bounds.TopLeft + new Vec2(40, 40), Vec2.Zero, Color.White);
}
