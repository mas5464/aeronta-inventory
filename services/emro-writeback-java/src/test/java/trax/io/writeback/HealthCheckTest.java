package trax.io.writeback;

import io.quarkus.test.junit.QuarkusTest;
import org.junit.jupiter.api.Test;
import static io.restassured.RestAssured.given;
import static org.hamcrest.CoreMatchers.is;

@QuarkusTest
class HealthCheckTest {
    @Test
    void health_is_up_and_anonymous() {
        given().when().get("/q/health").then().statusCode(200).body("status", is("UP"));
    }
}
