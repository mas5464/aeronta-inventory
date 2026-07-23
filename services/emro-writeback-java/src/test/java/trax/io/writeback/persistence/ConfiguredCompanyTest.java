package trax.io.writeback.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.quarkus.test.junit.QuarkusTest;
import io.quarkus.test.junit.QuarkusTestProfile;
import io.quarkus.test.junit.TestProfile;
import jakarta.inject.Inject;
import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * Pins the {@code writeback.emro.company} override on {@link TraxRepository#company()} — added
 * after the live eMRO smoke run found a multi-profile install (4 PROFILE_MASTER rows: ACA, ANEM,
 * ANT, TRAX), where the single-row lookup can only ever guess. When the property is set, it wins
 * unconditionally and no PROFILE_MASTER query result can override it.
 */
@QuarkusTest
@TestProfile(ConfiguredCompanyTest.CompanyOverrideProfile.class)
class ConfiguredCompanyTest {

    @Inject TraxRepository repo;

    @Test
    void configured_company_wins_over_profile_master_lookup() {
        assertEquals("ACA", repo.company());
    }

    public static class CompanyOverrideProfile implements QuarkusTestProfile {
        @Override
        public Map<String, String> getConfigOverrides() {
            return Map.of("writeback.emro.company", "ACA");
        }
    }
}
