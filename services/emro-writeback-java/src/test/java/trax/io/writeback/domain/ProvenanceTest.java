package trax.io.writeback.domain;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ProvenanceTest {
    @Test
    void batch_key_is_runId_colon_rowId() {
        var p = new Provenance("acme", "optimizer", "run-7", 42L, null, null, null, null, "svc");
        assertEquals("run-7:42", p.idempotencyKey());
    }

    @Test
    void explicit_key_wins_over_derivation() {
        var p = new Provenance("acme", "agent-spine", null, null, "prov-1", "explicit-key-1", 2, null, "svc");
        assertEquals("explicit-key-1", p.idempotencyKey());
    }

    @Test
    void missing_both_key_sources_throws() {
        var p = new Provenance("acme", "optimizer", null, 42L, null, null, null, null, "svc");
        assertThrows(IllegalStateException.class, p::idempotencyKey);
    }
}
