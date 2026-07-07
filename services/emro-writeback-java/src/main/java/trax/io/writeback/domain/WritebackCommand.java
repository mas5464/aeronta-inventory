package trax.io.writeback.domain;

public record WritebackCommand(
    String pn,
    String location,
    LevelValues levels,
    Provenance provenance,
    boolean shadow
) {
}
